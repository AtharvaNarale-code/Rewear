# ReWear — community clothing exchange (Flask prototype)

```bash
pip install -r requirements.txt
python app.py          # http://127.0.0.1:5000
```

The SQLite DB (`rewear.db`) is created and seeded on first start. Delete it to reset.

## Demo accounts
| Role   | Email               | Password      |
|--------|---------------------|---------------|
| Admin  | admin@rewear.com    | admin123      |
| Member | aarav@example.com   | password123   |
| Member | priya@example.com   | password123   |

New sign-ups get 50 points.

## How the exchange works
- Listings start `pending`; an admin approves (`available`) or rejects (`rejected`). Admin listings skip review.
- **Redeem**: buyer's points are held in escrow and the item is `reserved`. Seller accepts, buyer confirms receipt, then the seller is paid and the item is `completed`. Decline/cancel refunds the points and frees the item.
- **Direct swap**: buyer offers one of their available items; both items are reserved. Accept, then the buyer confirms and both items become `completed`.

## Layout
- `app.py` — models, routes, JSON API, seed data, placeholder SVG artwork
- `templates/` — Jinja pages (`base`, `index`, `browse`, `item_detail`, `dashboard`, `add_item`, `admin`, auth, error)
- `static/css/style.css` — design system (forest green `#2D5A27`, off-white `#F8F9FA`, charcoal `#212529`)
- `static/js/app.js` — fetch actions, modals, tabs, gallery, add-item wizard

## Notes
- Seed items use generated SVG illustrations (`/img/<type>/<hex>.svg`) so it works offline; any http(s) image URL works too.
- The gallery thumbnails are zoomed crops of the one stored image (the model has a single `image_url`).
- CSRF protection is on for all POSTs. Set `REWEAR_SECRET_KEY` outside local dev.
- Extras beyond the spec'd fields: `User.is_active`, `User.created_at` (suspension, member-since).
