
/* ReWear front-end: dependency-free helpers. */
(() => {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  const csrf = () => $('meta[name="csrf-token"]')?.content || '';

  document.documentElement.classList.add('js');

  /* ---------- Toasts ---------- */
  function toast(msg, kind = '') {
    const region = $('#toast-region');
    if (!region) return;

    const el = document.createElement('div');
    el.className = 'toast ' + kind;
    el.textContent = msg;
    region.appendChild(el);

    setTimeout(() => {
      el.style.opacity = '0';
      el.style.transition = 'opacity .3s';
      setTimeout(() => el.remove(), 320);
    }, 3600);
  }

  /* ---------- API helper ---------- */
  async function api(url, data = {}) {
    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
          'X-CSRF-Token': csrf()
        },
        body: JSON.stringify(data),
        credentials: 'same-origin'
      });

      let json = {};
      try {
        json = await res.json();
      } catch (_) {}

      if (res.status === 401) {
        location.href = '/login?next=' +
          encodeURIComponent(location.pathname);
        return { ok: false, message: 'Please log in.' };
      }

      return {
        ...json,
        ok: res.ok && json.ok !== false,
        message: json.message ||
          (res.ok ? 'Done.' : 'Something went wrong.')
      };
    } catch (err) {
      return {
        ok: false,
        message: 'Network error — please try again.'
      };
    }
  }

  /* ---------- Modals ---------- */
  function openModal(el) {
    el.hidden = false;
    document.body.style.overflow = 'hidden';
    (el.querySelector('input,button.btn-primary') || el).focus?.();
  }

  function closeModal(el) {
    if (!el) return;
    el.hidden = true;
    document.body.style.overflow = '';
  }

  document.addEventListener('click', (e) => {
    const opener = e.target.closest('[data-open-modal]');

    if (opener) {
      const modal = document.getElementById(
        opener.dataset.openModal
      );
      if (modal) openModal(modal);
    }

    const closer = e.target.closest('[data-close-modal]');
    if (closer) closeModal(closer.closest('.modal'));

    if (e.target.classList?.contains('modal')) {
      closeModal(e.target);
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      $$('.modal:not([hidden])').forEach(closeModal);
      $$('.open').forEach((el) => el.classList.remove('open'));
      document.body.classList.remove('nav-open');
    }
  });

  function confirmDialog(message) {
    return new Promise((resolve) => {
      const modal = document.createElement('div');
      modal.className = 'modal';
      modal.setAttribute('role', 'alertdialog');
      modal.setAttribute('aria-modal', 'true');

      modal.innerHTML =
        '<div class="modal-card confirm-card">' +
        '<h3>Are you sure?</h3><p></p>' +
        '<div class="modal-actions">' +
        '<button class="btn btn-ghost" data-v="0">Cancel</button>' +
        '<button class="btn btn-primary" data-v="1">Confirm</button>' +
        '</div></div>';

      modal.querySelector('p').textContent = message;

      const done = (value) => {
        modal.remove();
        document.body.style.overflow = '';
        resolve(value);
      };

      modal.addEventListener('click', (e) => {
        const button = e.target.closest('[data-v]');

        if (button) {
          done(button.dataset.v === '1');
        } else if (e.target === modal) {
          done(false);
        }
      });

      document.addEventListener('keydown', function esc(e) {
        if (e.key === 'Escape') {
          document.removeEventListener('keydown', esc);
          if (modal.isConnected) done(false);
        }
      });

      document.body.appendChild(modal);
      document.body.style.overflow = 'hidden';
      modal.querySelector('[data-v="1"]').focus();
    });
  }

  /* ---------- Generic API action buttons ---------- */
  function updateBalance(value) {
    const pill = $('#points-pill');

    if (pill && typeof value === 'number') {
      pill.textContent = '🪙 ' + value + ' Pts';
    }
  }

  document.addEventListener('click', async (e) => {
    const button = e.target.closest('button[data-api]');
    if (!button || button.disabled) return;

    if (
      button.dataset.confirm &&
      !(await confirmDialog(button.dataset.confirm))
    ) return;

    const payload = {};

    if (button.dataset.input) {
      const input = $(button.dataset.input);
      payload[button.dataset.key || 'value'] =
        input ? input.value : '';
    }

    button.disabled = true;
    const original = button.textContent;
    button.textContent = '…';

    const result = await api(button.dataset.api, payload);

    toast(result.message, result.ok ? 'ok' : 'err');

    if (typeof result.balance === 'number') {
      updateBalance(result.balance);
    }

    if (result.ok) {
      if (button.hasAttribute('data-noreload')) {
        button.textContent = original;
        button.disabled = false;
        return;
      }

      setTimeout(() => {
        if (result.redirect) {
          location.href = result.redirect;
        } else {
          location.reload();
        }
      }, 700);
    } else {
      button.textContent = original;
      button.disabled = false;
    }
  });

  /* ---------- Swap form ---------- */
  $('#swap-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();

    const form = e.currentTarget;
    const chosen = form.querySelector(
      'input[name="offered_item_id"]:checked'
    );

    if (!chosen) {
      toast('Pick an item to offer.', 'err');
      return;
    }

    const submit = form.querySelector('[type="submit"]');
    submit.disabled = true;

    const result = await api(form.dataset.api, {
      offered_item_id: chosen.value
    });

    toast(result.message, result.ok ? 'ok' : 'err');

    if (result.ok) {
      setTimeout(() => {
        location.href = result.redirect || '/dashboard?tab=swaps';
      }, 800);
    } else {
      submit.disabled = false;
    }
  });

  /* ---------- Dropdowns and mobile navigation ---------- */
  const mobile = () =>
    window.matchMedia('(max-width: 900px)').matches;

  document.addEventListener('click', (e) => {
    const trigger = e.target.closest('.dd-trigger');

    if (trigger && mobile()) {
      e.preventDefault();
      trigger.parentElement.classList.toggle('open');
      return;
    }

    const profileButton = e.target.closest('.profile-btn');

    if (profileButton) {
      const wrapper = profileButton.closest('[data-dropdown]');
      const isOpen = wrapper.classList.toggle('open');
      profileButton.setAttribute('aria-expanded', isOpen);
      return;
    }

    if (!e.target.closest('[data-dropdown]')) {
      $$('[data-dropdown].open').forEach((el) =>
        el.classList.remove('open')
      );
    }

    const toggle = e.target.closest('.nav-toggle');

    if (toggle) {
      const isOpen = document.body.classList.toggle('nav-open');
      toggle.setAttribute('aria-expanded', isOpen);
    } else if (
      document.body.classList.contains('nav-open') &&
      !e.target.closest('.primary-nav')
    ) {
      document.body.classList.remove('nav-open');
      $('.nav-toggle')?.setAttribute('aria-expanded', 'false');
    }

    const dismiss = e.target.closest('.flash-x');
    if (dismiss) dismiss.parentElement.remove();
  });

  /* ---------- Carousel ---------- */
  $$('[data-carousel]').forEach((carousel) => {
    const step = () => Math.max(carousel.clientWidth * 0.8, 240);

    $('[data-carousel-prev]')?.addEventListener('click', () => {
      carousel.scrollBy({
        left: -step(),
        behavior: 'smooth'
      });
    });

    $('[data-carousel-next]')?.addEventListener('click', () => {
      carousel.scrollBy({
        left: step(),
        behavior: 'smooth'
      });
    });
  });

  /* ---------- Browse filters ---------- */
  const tidy = (form) => {
    $$('input, select', form).forEach((input) => {
      if (input.type === 'radio' && !input.checked) return;

      if (
        input.value === '' ||
        (input.type === 'range' && input.value === input.max)
      ) {
        input.disabled = true;
      }
    });
  };

  $$('form[data-autosubmit]').forEach((form) => {
    form.addEventListener('change', () => {
      tidy(form);
      form.submit();
    });

    form.addEventListener('submit', () => tidy(form));
  });

  window.addEventListener('pageshow', () => {
    $$('form[data-autosubmit] [disabled]').forEach((input) => {
      input.disabled = false;
    });
  });

  $('[data-toggle-filters]')?.addEventListener('click', () => {
    $('#filters')?.classList.toggle('open');
  });

  /* ---------- Item detail gallery ---------- */
  $$('[data-gallery]').forEach((gallery) => {
    const main = $('[data-main] img', gallery);
    const frame = $('[data-main]', gallery);

    if (!main || !frame) return;

    let scale = 1;
    let originX = '50%';
    let originY = '50%';
    let zooming = false;

    const apply = () => {
      main.style.transformOrigin = `${originX} ${originY}`;
      main.style.transform = `scale(${scale})`;
    };

    $$('.thumb', gallery).forEach((thumb) => {
      thumb.addEventListener('click', () => {
        $$('.thumb', gallery).forEach((el) =>
          el.classList.remove('active')
        );

        thumb.classList.add('active');

        scale = parseFloat(thumb.dataset.scale) || 1;
        originX = thumb.dataset.ox || '50%';
        originY = thumb.dataset.oy || '50%';
        zooming = false;

        apply();
      });
    });

    frame.addEventListener('mouseenter', () => {
      if (window.matchMedia('(hover:hover)').matches) {
        zooming = true;
      }
    });

    frame.addEventListener('mousemove', (e) => {
      if (!zooming) return;

      const rect = frame.getBoundingClientRect();

      main.style.transformOrigin =
        `${((e.clientX - rect.left) / rect.width) * 100}% ` +
        `${((e.clientY - rect.top) / rect.height) * 100}%`;

      main.style.transform = `scale(${Math.max(scale, 1.8)})`;
    });

    frame.addEventListener('mouseleave', () => {
      zooming = false;
      apply();
    });
  });

  /* ---------- Dashboard tabs ---------- */
  $$('[data-tabs]').forEach((root) => {
    const tabs = $$('.tab', root);
    const panels = $$('.tab-panel', root);

    const show = (name, push) => {
      if (!panels.some((panel) => panel.dataset.panel === name)) {
        name = 'overview';
      }

      tabs.forEach((tab) => {
        const active = tab.dataset.tab === name;
        tab.classList.toggle('active', active);
        tab.setAttribute('aria-selected', active);
      });

      panels.forEach((panel) => {
        panel.classList.toggle(
          'active',
          panel.dataset.panel === name
        );
      });

      if (push) {
        const url = new URL(location.href);
        url.searchParams.set('tab', name);
        history.replaceState(null, '', url);
      }
    };

    tabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        show(tab.dataset.tab, true);
      });
    });

    show(root.dataset.initial || 'overview', false);
  });

  /*
   * IMPORTANT:
   * The add-item wizard is deliberately not initialized here.
   * Its photo-upload preview and step navigation are handled
   * by add_item.html, which uses the selected File object.
   *
   * Do not restore the old #f-image URL preview code here:
   * a file input's .value is not a usable image URL.
   */

})();
