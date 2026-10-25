
from dotenv import load_dotenv

load_dotenv()
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (Flask, Response, abort, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not configured.")

if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgresql://",
        "postgresql+psycopg://",
        1,
    )

app.config.update(
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,
    SECRET_KEY=os.environ["REWEAR_SECRET_KEY"],
    SQLALCHEMY_DATABASE_URI=DATABASE_URL,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
)

db = SQLAlchemy(app)


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
SIGNUP_BONUS = 50
GENDERS = ["Men", "Women", "Kids"]
TYPES = ["Upperwear", "Lowerwear", "Footwear", "Accessories"]
CONDITIONS = ["New with Tags", "Like New", "Good", "Fair"]
SIZE_GROUPS = {
    "Clothing": ["XS", "S", "M", "L", "XL", "XXL"],
    "Kids": ["2-4Y", "5-7Y", "8-10Y", "11-13Y"],
    "Footwear": ["UK 3", "UK 4", "UK 5", "UK 6", "UK 7", "UK 8", "UK 9", "UK 10", "UK 11"],
    "Other": ["One Size"],
}
SIZE_ORDER = [s for group in SIZE_GROUPS.values() for s in group]
POINT_RANGES = {
    "under50": ("Under 50 pts", lambda c: c < 50),
    "50-100": ("50–100 pts", lambda c: 50 <= c < 100),
    "100plus": ("100+ pts", lambda c: c >= 100),
}
NAV_CATEGORIES = [{"name": g_, "subs": TYPES} for g_ in GENDERS]
PLACEHOLDER_COLORS = ["2D5A27", "6B8F71", "B08968", "3D5A80", "C97B63",
                      "8E7DBE", "D4A373", "4A4E69", "A44A3F", "5F7470"]
PER_PAGE = 12


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    points_balance = db.Column(db.Integer, nullable=False, default=SIGNUP_BONUS)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    # Extras used for moderation / display
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

    @property
    def initial(self):
        return (self.name or "?")[:1].upper()


class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    uploader_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, default="")
    gender_category = db.Column(db.String(20), nullable=False)
    clothing_type = db.Column(db.String(30), nullable=False)
    size = db.Column(db.String(20), nullable=False)
    condition = db.Column(db.String(30), nullable=False)
    tags = db.Column(db.String(255), default="")
    point_value = db.Column(db.Integer, nullable=False, default=30)
    image_url = db.Column(db.String(500), default="")
    # pending -> available -> reserved -> completed   (or pending -> rejected)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    uploader = db.relationship("User", backref="items")

    @property
    def tag_list(self):
        return [t.strip() for t in (self.tags or "").split(",") if t.strip()]

    @property
    def fallback_image(self):
        color = PLACEHOLDER_COLORS[sum(map(ord, self.title or "x")) % len(PLACEHOLDER_COLORS)]
        return placeholder_path(self.clothing_type, color)

    @property
    def image(self):
        return self.image_url or self.fallback_image


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    buyer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    type = db.Column(db.String(10), nullable=False)  # 'redeem' | 'swap'
    offered_item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=True)
    points_spent = db.Column(db.Integer, nullable=False, default=0)
    # pending -> accepted -> completed   |  declined | cancelled
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    item = db.relationship("Item", foreign_keys=[item_id])
    offered_item = db.relationship("Item", foreign_keys=[offered_item_id])
    buyer = db.relationship("User", foreign_keys=[buyer_id])
    seller = db.relationship("User", foreign_keys=[seller_id])


# --------------------------------------------------------------------------- #
# Placeholder artwork (so the prototype works offline, no stock photos needed)
# --------------------------------------------------------------------------- #
def placeholder_path(clothing_type, color="2D5A27"):
    kind = (clothing_type or "upperwear").lower()
    if kind not in ("upperwear", "lowerwear", "footwear", "accessories"):
        kind = "upperwear"
    return f"/img/{kind}/{color}.svg"


def _shade(hex_color, t, toward=(255, 255, 255)):
    rgb = [int(hex_color[i:i + 2], 16) for i in (0, 2, 4)]
    return "#%02x%02x%02x" % tuple(round(c + (o - c) * t) for c, o in zip(rgb, toward))


def _shape_upperwear(c, d):
    return (
        f'<path d="M135 80 L172 62 Q200 92 228 62 L265 80 L335 128 L300 178 L272 156 '
        f'L272 410 L128 410 L128 156 L100 178 L65 128 Z" fill="{c}"/>'
        f'<path d="M172 62 Q200 92 228 62" fill="none" stroke="{d}" stroke-width="5"/>'
        f'<path d="M128 396 H272" stroke="{d}" stroke-width="3" stroke-dasharray="7 6" opacity=".7"/>'
        f'<path d="M128 156 L100 178 M272 156 L300 178" stroke="{d}" stroke-width="3" opacity=".5"/>'
    )


def _shape_lowerwear(c, d):
    return (
        f'<path d="M128 70 L272 70 L292 432 L212 432 L200 204 L188 432 L108 432 Z" fill="{c}"/>'
        f'<rect x="128" y="70" width="144" height="26" fill="{d}" opacity=".35"/>'
        f'<path d="M200 96 V204" stroke="{d}" stroke-width="3" opacity=".7"/>'
        f'<path d="M150 118 q20 18 40 4 M250 118 q-20 18 -40 4" fill="none" stroke="{d}" stroke-width="3" opacity=".6"/>'
        f'<path d="M110 418 H188 M212 418 H290" stroke="{d}" stroke-width="3" stroke-dasharray="7 6" opacity=".7"/>'
    )


def _shape_footwear(c, d):
    return (
        f'<path d="M58 330 L58 250 Q112 244 150 196 Q170 176 196 196 Q250 250 330 262 '
        f'Q356 270 356 304 L356 330 Z" fill="{c}"/>'
        f'<rect x="48" y="324" width="320" height="28" rx="14" fill="{d}"/>'
        f'<path d="M166 216 L190 238 M184 202 L208 224 M202 192 L226 214" stroke="#fff" '
        f'stroke-width="6" stroke-linecap="round" opacity=".85"/>'
        f'<path d="M58 290 H300" stroke="{d}" stroke-width="3" stroke-dasharray="6 6" opacity=".6"/>'
    )


def _shape_accessories(c, d):
    return (
        f'<path d="M150 190 Q150 96 200 96 Q250 96 250 190" fill="none" stroke="{d}" '
        f'stroke-width="11" stroke-linecap="round"/>'
        f'<path d="M105 180 L295 180 L320 424 L80 424 Z" fill="{c}"/>'
        f'<rect x="150" y="270" width="100" height="70" rx="8" fill="{d}" opacity=".35"/>'
        f'<path d="M105 180 L295 180" stroke="{d}" stroke-width="4"/>'
    )


SHAPES = {
    "upperwear": _shape_upperwear,
    "lowerwear": _shape_lowerwear,
    "footwear": _shape_footwear,
    "accessories": _shape_accessories,
}


@app.route("/img/<kind>/<color>.svg")
def placeholder(kind, color):
    if kind not in SHAPES or not re.fullmatch(r"[0-9a-fA-F]{6}", color):
        abort(404)
    main = "#" + color
    dark = _shade(color, 0.35, (0, 0, 0))
    bg1, bg2 = _shade(color, 0.88), _shade(color, 0.72)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 500" preserveAspectRatio="xMidYMid slice">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{bg1}"/><stop offset="1" stop-color="{bg2}"/></linearGradient></defs>'
        '<rect width="400" height="500" fill="url(#g)"/>'
        '<ellipse cx="200" cy="452" rx="120" ry="14" fill="#000" opacity=".10"/>'
        f'{SHAPES[kind](main, dark)}'
        '</svg>'
    )
    return Response(svg, mimetype="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


# --------------------------------------------------------------------------- #
# Request plumbing: current user, CSRF, helpers, error pages
# --------------------------------------------------------------------------- #
@app.before_request
def load_user_and_check_csrf():
    g.user = db.session.get(User, session["user_id"]) if session.get("user_id") else None
    if g.user and not g.user.is_active:
        session.clear()
        g.user = None
    if request.method == "POST":
        sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or ""
        expected = session.get("csrf", "")
        if not expected or not secrets.compare_digest(sent, expected):
            abort(400, "Your session expired. Please refresh the page and try again.")


@app.context_processor
def inject_globals():
    def csrf_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return session["csrf"]

    return dict(
        csrf_token=csrf_token,
        nav_categories=NAV_CATEGORIES,
        point_ranges={k: v[0] for k, v in POINT_RANGES.items()},
        GENDERS=GENDERS, TYPES=TYPES, CONDITIONS=CONDITIONS, SIZE_GROUPS=SIZE_GROUPS,
    )


@app.template_filter("dt")
def fmt_date(value):
    return value.strftime("%d %b %Y") if value else ""


def safe_next(target):
    return target if target and target.startswith("/") and not target.startswith("//") else None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            if request.path.startswith("/api/"):
                return jsonify(ok=False, message="Please log in first."), 401
            flash("Please log in to continue.", "info")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not g.user.is_admin:
            abort(403, "Admins only.")
        return view(*args, **kwargs)
    return wrapped


@app.errorhandler(413)
def upload_too_large(err):
    message = "Image is too large. Please upload an image smaller than 5 MB."
    if request.path.startswith("/api/"):
        return jsonify(ok=False, message=message), 413
    flash(message, "error")
    return redirect(url_for("add_item")), 413


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
def http_error(err):
    if request.path.startswith("/api/"):
        return jsonify(ok=False, message=err.description), err.code
    return render_template("error.html", code=err.code, name=err.name,
                           message=err.description), err.code


# --------------------------------------------------------------------------- #
# Exchange logic (points are held in escrow while a request is open)
# --------------------------------------------------------------------------- #
def start_redeem(item, buyer):
    if item.status != "available":
        return False, "Sorry, this item is no longer available."
    if item.uploader_id == buyer.id:
        return False, "You can't redeem your own item."
    if buyer.points_balance < item.point_value:
        return False, f"You need {item.point_value - buyer.points_balance} more points to redeem this."
    buyer.points_balance -= item.point_value
    item.status = "reserved"
    db.session.add(Transaction(item=item, buyer=buyer, seller=item.uploader, type="redeem",
                               points_spent=item.point_value, status="pending"))
    return True, "Redemption requested! Your points are held until the swap completes."


def start_swap(item, buyer, offered):
    if item.status != "available":
        return False, "Sorry, this item is no longer available."
    if item.uploader_id == buyer.id:
        return False, "You can't swap with yourself."
    if not offered or offered.uploader_id != buyer.id or offered.status != "available":
        return False, "Please choose one of your approved, available items to offer."
    item.status = "reserved"
    offered.status = "reserved"
    db.session.add(Transaction(item=item, buyer=buyer, seller=item.uploader, type="swap",
                               offered_item=offered, points_spent=0, status="pending"))
    return True, "Swap request sent! Both items are reserved until the owner responds."


def _release(tx, new_status):
    for it in (tx.item, tx.offered_item):
        if it is not None and it.status == "reserved":
            it.status = "available"
    if tx.type == "redeem":
        tx.buyer.points_balance += tx.points_spent  # refund escrow
    tx.status = new_status


def apply_action(tx, user, action):
    if action == "accept":
        if user.id != tx.seller_id or tx.status != "pending":
            return False, "This request can't be accepted."
        tx.status = "accepted"
        return True, "Request accepted. The buyer can confirm once the exchange is done."
    if action == "decline":
        if user.id != tx.seller_id or tx.status != "pending":
            return False, "This request can't be declined."
        _release(tx, "declined")
        return True, "Request declined. The item is available again."
    if action == "cancel":
        if user.id != tx.buyer_id or tx.status not in ("pending", "accepted"):
            return False, "This request can't be cancelled."
        _release(tx, "cancelled")
        return True, "Request cancelled" + (" and your points were refunded." if tx.type == "redeem" else ".")
    if action == "confirm":
        if user.id != tx.buyer_id or tx.status != "accepted":
            return False, "Only the buyer can confirm an accepted request."
        tx.status = "completed"
        tx.item.status = "completed"
        if tx.offered_item is not None:
            tx.offered_item.status = "completed"
        if tx.type == "redeem":
            tx.seller.points_balance += tx.points_spent
        return True, "Exchange completed — thanks for keeping clothes in circulation! ♻️"
    return False, "Unknown action."


def points_history(user, incoming, outgoing):
    rows = [{"date": user.created_at, "label": "Welcome bonus", "delta": SIGNUP_BONUS, "note": "", "pending": False}]
    for t in outgoing:
        if t.type != "redeem":
            continue
        if t.status in ("declined", "cancelled"):
            rows.append({"date": t.created_at, "label": f"Redeemed “{t.item.title}”", "delta": 0,
                         "note": f"{t.status.capitalize()} — {t.points_spent} pts refunded", "pending": False})
        else:
            rows.append({"date": t.created_at, "label": f"Redeemed “{t.item.title}”", "delta": -t.points_spent,
                         "note": "Held in escrow" if t.status != "completed" else "Completed",
                         "pending": t.status != "completed"})
    for t in incoming:
        if t.type == "redeem" and t.status not in ("declined", "cancelled"):
            rows.append({"date": t.created_at, "label": f"Sold “{t.item.title}”", "delta": t.points_spent,
                         "note": "Paid on completion" if t.status != "completed" else "Completed",
                         "pending": t.status != "completed"})
    rows.sort(key=lambda r: r["date"] or datetime.min, reverse=True)
    return rows


# --------------------------------------------------------------------------- #
# Public pages
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    featured = (Item.query.filter_by(status="available")
                .order_by(Item.created_at.desc()).limit(8).all())
    stats = {
        "items": Item.query.filter_by(status="available").count(),
        "members": User.query.count(),
        "swaps": Transaction.query.filter_by(status="completed").count(),
    }
    return render_template("index.html", featured=featured, stats=stats)


@app.route("/browse")
def browse():
    args = request.args
    gender = args.get("gender") if args.get("gender") in GENDERS else None
    ctype = args.get("type") if args.get("type") in TYPES else None
    condition = args.get("condition") if args.get("condition") in CONDITIONS else None
    size = args.get("size") or None
    pts_range = args.get("points") if args.get("points") in POINT_RANGES else None
    max_points = args.get("max_points", type=int)
    q = (args.get("q") or "").strip()
    sort = args.get("sort", "newest")
    page = max(args.get("page", 1, type=int) or 1, 1)

    query = Item.query.filter(Item.status == "available")
    if gender:
        query = query.filter(Item.gender_category == gender)
    if ctype:
        query = query.filter(Item.clothing_type == ctype)
    if condition:
        query = query.filter(Item.condition == condition)
    if size:
        query = query.filter(Item.size == size)
    if pts_range == "under50":
        query = query.filter(Item.point_value < 50)
    elif pts_range == "50-100":
        query = query.filter(Item.point_value >= 50, Item.point_value < 100)
    elif pts_range == "100plus":
        query = query.filter(Item.point_value >= 100)
    if max_points is not None:
        query = query.filter(Item.point_value <= max_points)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Item.title.ilike(like), Item.tags.ilike(like), Item.description.ilike(like)))

    order = {"points_asc": Item.point_value.asc(), "points_desc": Item.point_value.desc()}.get(
        sort, Item.created_at.desc())
    pagination = query.order_by(order, Item.id.desc()).paginate(page=page, per_page=PER_PAGE, error_out=False)

    used_sizes = {r[0] for r in db.session.query(Item.size).filter(Item.status == "available").distinct()}
    sizes = [s for s in SIZE_ORDER if s in used_sizes] + sorted(used_sizes - set(SIZE_ORDER))
    ceiling = db.session.query(db.func.max(Item.point_value)).scalar() or 100
    ceiling = ((ceiling + 9) // 10) * 10
    if max_points is not None and max_points >= ceiling:
        max_points = None  # slider at its maximum = no limit (avoids a pointless filter chip)

    chips = []
    labels = {"gender": gender, "type": ctype, "condition": condition, "size": size and f"Size {size}",
              "points": pts_range and POINT_RANGES[pts_range][0],
              "max_points": max_points is not None and f"≤ {max_points} pts", "q": q and f"“{q}”"}
    for key, label in labels.items():
        if label:
            rest = {k: v for k, v in args.to_dict().items() if k not in (key, "page")}
            chips.append((label, url_for("browse", **rest)))

    return render_template(
        "browse.html", pagination=pagination, items=pagination.items, sizes=sizes, ceiling=ceiling,
        f=dict(gender=gender, type=ctype, condition=condition, size=size, points=pts_range,
               max_points=max_points if max_points is not None else ceiling, q=q, sort=sort),
        chips=chips)


@app.route("/item/<int:item_id>")
def item_detail(item_id):
    item = db.get_or_404(Item, item_id)
    if item.status in ("pending", "rejected"):
        if not (g.user and (g.user.is_admin or g.user.id == item.uploader_id)):
            abort(404, "That item isn't available.")
    my_items = []
    if g.user:
        my_items = Item.query.filter_by(uploader_id=g.user.id, status="available").order_by(Item.title).all()
    related = (Item.query.filter(Item.status == "available", Item.id != item.id,
                                 or_(Item.clothing_type == item.clothing_type,
                                     Item.gender_category == item.gender_category))
               .order_by(Item.created_at.desc()).limit(4).all())
    return render_template("item_detail.html", item=item, my_items=my_items, related=related)


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        error = None
        if len(name) < 2:
            error = "Please enter your name."
        elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            error = "Please enter a valid email address."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif User.query.filter_by(email=email).first():
            error = "An account with that email already exists."
        if error:
            flash(error, "error")
            return render_template("register.html", form=request.form), 400
        user = User(name=name, email=email, points_balance=SIGNUP_BONUS)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        session.clear()
        session["user_id"] = user.id
        flash(f"Welcome to ReWear, {user.name.split()[0]}! We've added {SIGNUP_BONUS} points to your balance 🪙", "success")
        return redirect(url_for("browse"))
    return render_template("register.html", form={})


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("dashboard"))
    nxt = safe_next(request.values.get("next"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(request.form.get("password", "")):
            flash("Incorrect email or password.", "error")
            return render_template("login.html", next=nxt, email=email), 401
        if not user.is_active:
            flash("This account has been suspended. Contact support.", "error")
            return render_template("login.html", next=nxt, email=email), 403
        session.clear()
        session["user_id"] = user.id
        flash(f"Welcome back, {user.name.split()[0]}!", "success")
        return redirect(nxt or url_for("dashboard"))
    return render_template("login.html", next=nxt, email="")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You've been logged out.", "info")
    return redirect(url_for("index"))


# --------------------------------------------------------------------------- #
# Member pages
# --------------------------------------------------------------------------- #
@app.route("/dashboard")
@login_required
def dashboard():
    tab = request.args.get("tab", "overview")
    if tab not in ("overview", "items", "swaps"):
        tab = "overview"
    mine = Item.query.filter_by(uploader_id=g.user.id).order_by(Item.created_at.desc()).all()
    groups = {
        "pending": [i for i in mine if i.status == "pending"],
        "approved": [i for i in mine if i.status in ("available", "reserved")],
        "sold": [i for i in mine if i.status == "completed"],
        "rejected": [i for i in mine if i.status == "rejected"],
    }
    incoming = Transaction.query.filter_by(seller_id=g.user.id).order_by(Transaction.created_at.desc()).all()
    outgoing = Transaction.query.filter_by(buyer_id=g.user.id).order_by(Transaction.created_at.desc()).all()
    live = ("pending", "accepted")
    stats = {
        "listed": len(mine),
        "active": sum(1 for t in incoming + outgoing if t.status in live),
        "completed": sum(1 for t in incoming + outgoing if t.status == "completed"),
        "awaiting": sum(1 for t in incoming if t.status == "pending"),
    }
    return render_template(
        "dashboard.html", tab=tab, groups=groups, stats=stats,
        incoming_active=[t for t in incoming if t.status in live],
        outgoing_active=[t for t in outgoing if t.status in live],
        past=sorted([t for t in incoming + outgoing if t.status not in live],
                    key=lambda t: t.created_at, reverse=True),
        history=points_history(g.user, incoming, outgoing))


@app.route("/items/new", methods=["GET", "POST"])
@login_required
def add_item():
    form = request.form if request.method == "POST" else {}
    if request.method == "POST":
        title = form.get("title", "").strip()
        desc = form.get("description", "").strip()
        gender, ctype = form.get("gender_category"), form.get("clothing_type")
        size, condition = form.get("size"), form.get("condition")
        tags = ", ".join(t.strip().lower() for t in form.get("tags", "").split(",") if t.strip())[:255]
        uploaded_image = request.files.get("image")
        points = form.get("point_value", type=int)
        error = None

        # Validate an optional image upload. Empty uploads use the existing placeholder.
        image_url = ""
        if uploaded_image and uploaded_image.filename:
            original_name = secure_filename(uploaded_image.filename)
            extension = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
            if extension not in ALLOWED_IMAGE_EXTENSIONS:
                error = "Please upload a JPG, JPEG, PNG, WEBP, or GIF image."
            elif uploaded_image.mimetype not in {
                "image/jpeg", "image/png", "image/webp", "image/gif"
            }:
                error = "The uploaded file must be a valid image."
            else:
                # Save only after the remaining form fields have passed validation.
                image_url = "/static/uploads/" + uuid.uuid4().hex + "." + extension
        if len(title) < 3:
            error = "Please give your item a title (at least 3 characters)."
        elif gender not in GENDERS or ctype not in TYPES:
            error = "Please choose a gender category and clothing type."
        elif size not in SIZE_ORDER:
            error = "Please choose a size."
        elif condition not in CONDITIONS:
            error = "Please choose the item's condition."
        elif points is None or not 1 <= points <= 1000:
            error = "Requested points must be between 1 and 1000."
        if error:
            flash(error, "error")
            return render_template("add_item.html", form=form), 400
        # Persist the file only once all form validation has succeeded.
        if uploaded_image and uploaded_image.filename and image_url:
            saved_path = os.path.join(BASE_DIR, image_url.lstrip("/"))
            uploaded_image.save(saved_path)

        item = Item(uploader_id=g.user.id, title=title, description=desc, gender_category=gender,
                    clothing_type=ctype, size=size, condition=condition, tags=tags,
                    point_value=points, image_url=image_url,
                    status="available" if g.user.is_admin else "pending")
        db.session.add(item)
        db.session.commit()
        if g.user.is_admin:
            flash("Listing published (admin listings skip review).", "success")
            return redirect(url_for("item_detail", item_id=item.id))
        flash("Thanks! Your item was submitted and is awaiting admin approval.", "success")
        return redirect(url_for("dashboard", tab="items"))
    return render_template("add_item.html", form=form)


# --------------------------------------------------------------------------- #
# JSON API (called with fetch from static/js/app.js)
# --------------------------------------------------------------------------- #
@app.post("/api/item/<int:item_id>/redeem")
@login_required
def api_redeem(item_id):
    item = db.get_or_404(Item, item_id)
    ok, msg = start_redeem(item, g.user)
    if ok:
        db.session.commit()
    return jsonify(ok=ok, message=msg, balance=g.user.points_balance,
                   redirect=url_for("dashboard", tab="swaps") if ok else None), (200 if ok else 400)


@app.post("/api/item/<int:item_id>/swap")
@login_required
def api_swap(item_id):
    item = db.get_or_404(Item, item_id)
    data = request.get_json(silent=True) or {}
    try:
        offered = db.session.get(Item, int(data.get("offered_item_id")))
    except (TypeError, ValueError):
        offered = None
    ok, msg = start_swap(item, g.user, offered)
    if ok:
        db.session.commit()
    return jsonify(ok=ok, message=msg, redirect=url_for("dashboard", tab="swaps") if ok else None), (200 if ok else 400)


@app.post("/api/transaction/<int:tx_id>/<action>")
@login_required
def api_transaction(tx_id, action):
    tx = db.get_or_404(Transaction, tx_id)
    if g.user.id not in (tx.buyer_id, tx.seller_id):
        abort(403, "That request doesn't belong to you.")
    ok, msg = apply_action(tx, g.user, action)
    if ok:
        db.session.commit()
    return jsonify(ok=ok, message=msg, balance=g.user.points_balance), (200 if ok else 400)


# --------------------------------------------------------------------------- #
# Admin
# --------------------------------------------------------------------------- #
@app.route("/admin")
@admin_required
def admin():
    stats = {
        "users": User.query.count(),
        "listings": Item.query.count(),
        "pending": Item.query.filter_by(status="pending").count(),
        "swaps": Transaction.query.filter_by(status="completed").count(),
    }
    pending = Item.query.filter_by(status="pending").order_by(Item.created_at.asc()).all()
    users = User.query.order_by(User.id).all()
    recent = Transaction.query.order_by(Transaction.created_at.desc()).limit(8).all()
    return render_template("admin.html", stats=stats, pending=pending, users=users, recent=recent)


@app.post("/api/admin/item/<int:item_id>/<action>")
@admin_required
def api_admin_item(item_id, action):
    item = db.get_or_404(Item, item_id)
    if action == "approve":
        if item.status != "pending":
            return jsonify(ok=False, message="Only pending items can be approved."), 400
        item.status = "available"
        msg = f"“{item.title}” approved and live."
    elif action == "reject":
        if item.status != "pending":
            return jsonify(ok=False, message="Only pending items can be rejected."), 400
        item.status = "rejected"
        msg = f"“{item.title}” rejected."
    elif action == "points":
        data = request.get_json(silent=True) or {}
        try:
            pts = int(data.get("points"))
        except (TypeError, ValueError):
            return jsonify(ok=False, message="Enter a whole number of points."), 400
        if not 1 <= pts <= 1000:
            return jsonify(ok=False, message="Points must be between 1 and 1000."), 400
        if item.status not in ("pending", "available"):
            return jsonify(ok=False, message="Points can't be changed on reserved or closed items."), 400
        item.point_value = pts
        msg = f"Point value updated to {pts}."
    else:
        abort(404)
    db.session.commit()
    return jsonify(ok=True, message=msg)


@app.post("/api/admin/user/<int:user_id>/<action>")
@admin_required
def api_admin_user(user_id, action):
    user = db.get_or_404(User, user_id)
    if action == "balance":
        data = request.get_json(silent=True) or {}
        try:
            delta = int(data.get("delta"))
        except (TypeError, ValueError):
            return jsonify(ok=False, message="Enter a whole number (use a minus sign to deduct)."), 400
        if delta == 0 or abs(delta) > 10000:
            return jsonify(ok=False, message="Enter a non-zero amount up to 10,000."), 400
        if user.points_balance + delta < 0:
            return jsonify(ok=False, message="Balance can't go below zero."), 400
        user.points_balance += delta
        msg = f"{user.name}'s balance is now {user.points_balance} pts."
    elif action == "toggle":
        if user.id == g.user.id:
            return jsonify(ok=False, message="You can't suspend your own account."), 400
        user.is_active = not user.is_active
        msg = f"{user.name} {'reactivated' if user.is_active else 'suspended'}."
    else:
        abort(404)
    db.session.commit()
    return jsonify(ok=True, message=msg)


# --------------------------------------------------------------------------- #
# Seed data
# --------------------------------------------------------------------------- #
def seed():
    if User.query.count():
        return
    now = utcnow()
    admin_u = User(name="ReWear Admin", email="admin@rewear.com", points_balance=500, is_admin=True,
                   created_at=now - timedelta(days=60))
    admin_u.set_password("admin123")
    aarav = User(name="Aarav Mehta", email="aarav@example.com", points_balance=120,
                 created_at=now - timedelta(days=30))
    aarav.set_password("password123")
    priya = User(name="Priya Nair", email="priya@example.com", points_balance=150,
                 created_at=now - timedelta(days=21))
    priya.set_password("password123")
    db.session.add_all([admin_u, aarav, priya])
    db.session.flush()

    def img(kind, color):
        return f"/img/{kind}/{color}.svg"

    # (owner, title, description, gender, type, size, condition, tags, points, color, status, days_ago)
    rows = [
        (aarav, "Classic Denim Jacket", "Mid-wash trucker jacket with brass buttons. Barely worn, soft broken-in feel. Great layering piece.",
         "Men", "Upperwear", "M", "Like New", "denim, jacket, casual, layering", 70, "3D5A80", "available", 2),
        (aarav, "Slim Fit Khaki Chinos", "Stretch cotton chinos in sand. Tapered leg, hemmed for 30\" inseam. Washed once a month.",
         "Men", "Lowerwear", "L", "Good", "chinos, office, smart-casual", 45, "B08968", "available", 4),
        (aarav, "Wool Blend Overcoat", "Charcoal single-breasted overcoat, fully lined. Perfect for winter commutes.",
         "Men", "Upperwear", "L", "Like New", "coat, winter, formal, wool", 140, "4A4E69", "available", 6),
        (aarav, "Vintage Band-Style Graphic Tee", "Faded black cotton tee with a retro print. Super soft.",
         "Men", "Upperwear", "M", "Good", "tee, graphic, vintage", 25, "4A4E69", "pending", 0),
        (aarav, "Cargo Shorts", "Olive cargo shorts with six pockets. Some fading on the hem.",
         "Men", "Lowerwear", "M", "Fair", "shorts, cargo, summer", 20, "5F7470", "pending", 0),
        (aarav, "Canvas Lace-Up Sneakers", "Off-white canvas sneakers, fresh insoles. Worn about a dozen times.",
         "Men", "Footwear", "UK 9", "Good", "sneakers, canvas, casual", 40, "D4A373", "available", 9),
        (aarav, "Kids Corduroy Dungarees", "Rust corduroy dungarees with adjustable straps. Outgrown too fast!",
         "Kids", "Lowerwear", "2-4Y", "Good", "kids, dungarees, corduroy", 30, "C97B63", "available", 7),
        (priya, "Floral Summer Blouse", "Lightweight viscose blouse with a sage floral print and tie neck.",
         "Women", "Upperwear", "S", "Like New", "blouse, floral, summer", 40, "6B8F71", "available", 1),
        (priya, "High-Rise Mom Jeans", "Rigid denim, high waist, relaxed leg. True to size, ankle length.",
         "Women", "Lowerwear", "M", "Good", "jeans, denim, high-rise", 55, "3D5A80", "available", 3),
        (priya, "Pleated Satin Midi Skirt", "Champagne satin midi skirt with elastic waist. Tags still attached.",
         "Women", "Lowerwear", "S", "New with Tags", "skirt, satin, party, midi", 65, "D4A373", "available", 5),
        (priya, "Chunky Knit Cardigan", "Oversized sage cardigan with wooden buttons. Warm and cosy.",
         "Women", "Upperwear", "M", "Good", "cardigan, knit, winter", 50, "6B8F71", "pending", 0),
        (priya, "Leather-Look Tote Bag", "Structured tote in tan, fits a 14\" laptop. Interior zip pocket.",
         "Women", "Accessories", "One Size", "Like New", "bag, tote, work", 50, "B08968", "available", 8),
        (priya, "Kids Quilted Puffer Jacket", "Warm padded jacket with fleece-lined hood. Mustard yellow.",
         "Kids", "Upperwear", "5-7Y", "Like New", "kids, puffer, winter", 45, "D4A373", "available", 10),
        (priya, "Kids Striped Sweatshirt", "Navy and cream stripe crew sweatshirt, soft brushed inside.",
         "Kids", "Upperwear", "8-10Y", "Good", "kids, stripes, sweatshirt", 20, "3D5A80", "available", 11),
        (priya, "Breton Striped Top", "Classic boat-neck stripe top in organic cotton.",
         "Women", "Upperwear", "S", "Good", "stripes, breton, classic", 35, "3D5A80", "available", 12),
    ]
    items = []
    for (owner, title, desc, gender, ctype, size, cond, tags, pts, color, status, days) in rows:
        it = Item(uploader=owner, title=title, description=desc, gender_category=gender,
                  clothing_type=ctype, size=size, condition=cond, tags=tags, point_value=pts,
                  image_url=img(ctype.lower(), color), status=status,
                  created_at=now - timedelta(days=days, hours=len(items)))
        items.append(it)
    db.session.add_all(items)
    db.session.flush()
    by_title = {i.title: i for i in items}

    # A finished redemption (Aarav bought Priya's Breton top) so history isn't empty.
    done = by_title["Breton Striped Top"]
    done.status = "completed"
    db.session.add(Transaction(item=done, buyer=aarav, seller=priya, type="redeem", points_spent=35,
                               status="completed", created_at=now - timedelta(days=5)))
    # Priya has redeemed Aarav's denim jacket (waiting for Aarav to accept).
    ok, _ = start_redeem(by_title["Classic Denim Jacket"], priya)
    # Aarav has asked to swap his chinos for Priya's floral blouse (waiting for Priya).
    ok2, _ = start_swap(by_title["Floral Summer Blouse"], aarav, by_title["Slim Fit Khaki Chinos"])
    db.session.commit()


with app.app_context():
    db.create_all()
    seed()

if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
