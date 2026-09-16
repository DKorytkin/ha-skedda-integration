/**
 * Skedda Scheduler panel.
 *
 * Shows what is booked, what is about to be booked, and which accounts can
 * still sign in. It performs exactly one action itself - releasing a booking -
 * because Home Assistant has no dialog to borrow for that. Everything else
 * opens the integration's own pages, so the forms, their validation and their
 * translations keep one implementation rather than two.
 *
 * Plain custom element, no build step: what ships is what was written.
 */

const LOGO =
  "M190.432 214.131L434.88 637.544C436.037 639.549 438.177 640.784 440.493 640.784L598.549 640.738C600.862 " +
  "640.737 602.999 639.503 604.157 637.5L683.252 500.622C684.411 498.617 684.411 496.146 683.253 494.141L438.798 " +
  "70.7235C437.641 68.7178 435.5 67.4827 433.184 67.4839L275.094 67.5642C272.781 67.5654 270.644 68.7995 269.487 " +
  "70.8024L190.433 207.651C189.275 209.655 189.275 212.126 190.432 214.131ZM167.616 640.625C246.717 640.625 " +
  "310.841 576.501 310.841 497.4C310.841 418.299 246.717 354.175 167.616 354.175C88.5147 354.175 24.3906 418.299 " +
  "24.3906 497.4C24.3906 576.501 88.5147 640.625 167.616 640.625Z";

const STRINGS = {
  en: {
    accounts: "Accounts",
    account: "Account",
    venue: "Venue",
    authorised: "authorised",
    signInProblem: "sign-in problem",
    manage: "Manage",
    manageJobs: "Manage",
    existingBookings: "Existing bookings",
    bookingJobs: "Booking jobs",
    when: "When",
    court: "Court",
    nextSlot: "Next slot",
    status: "Status",
    noBookings: "Nothing booked yet.",
    noJobs: "No booking jobs yet.",
    noAccounts: "No accounts yet.",
    cancel: "Cancel",
    cancelling: "Cancelling…",
    confirmCancel: "Release this court time?",
    loading: "Loading…",
    armed: "waiting for the window",
    disabled: "turned off",
    out_of_season: "out of season",
    opens: "opens",
    weekly: "weekly",
    once: "once",
    biweekly: "fortnightly",
  },
  uk: {
    accounts: "Акаунти",
    account: "Акаунт",
    venue: "Заклад",
    authorised: "авторизований",
    signInProblem: "помилка авторизації",
    manage: "Керувати",
    manageJobs: "Змінити",
    existingBookings: "Наявні бронювання",
    bookingJobs: "Завдання бронювання",
    when: "Коли",
    court: "Корт",
    nextSlot: "Наступний слот",
    status: "Стан",
    noBookings: "Ще нічого не заброньовано.",
    noJobs: "Ще немає жодного завдання.",
    noAccounts: "Ще немає жодного акаунта.",
    cancel: "Скасувати",
    cancelling: "Скасовую…",
    confirmCancel: "Звільнити цей час на корті?",
    loading: "Завантаження…",
    armed: "чекає на відкриття вікна",
    disabled: "вимкнено",
    out_of_season: "поза сезоном",
    opens: "відкриється",
    weekly: "щотижня",
    once: "один раз",
    biweekly: "раз на два тижні",
  },
};

const SETTINGS_URL = "/config/integrations/integration/skedda_scheduler";

class SkeddaPanel extends HTMLElement {
  set hass(hass) {
    this._hass = hass;
    if (!this._started) {
      this._started = true;
      this._render();
      this._refresh();
      // The scheduler arms and fires without anything asking, so the view has
      // to look again on its own or it quietly goes stale.
      this._timer = setInterval(() => this._refresh(), 30000);
    }
  }

  disconnectedCallback() {
    clearInterval(this._timer);
  }

  get _t() {
    const language = (this._hass?.language || "en").split("-")[0];
    return STRINGS[language] || STRINGS.en;
  }

  async _refresh() {
    try {
      this._data = await this._hass.callWS({ type: "skedda_scheduler/overview" });
      this._error = null;
    } catch (err) {
      this._error = err.message || String(err);
    }
    this._paint();
  }

  async _cancel(entryId, bookingId, button) {
    if (!confirm(this._t.confirmCancel)) return;
    button.disabled = true;
    button.textContent = this._t.cancelling;
    try {
      await this._hass.callWS({
        type: "skedda_scheduler/cancel_booking",
        entry_id: entryId,
        booking_id: bookingId,
      });
    } catch (err) {
      this._error = err.message || String(err);
    }
    await this._refresh();
  }

  _render() {
    this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          padding: 16px;
          background: var(--primary-background-color);
          min-height: 100%;
          box-sizing: border-box;
        }
        .wrap { max-width: 1100px; margin: 0 auto; }
        header {
          display: flex; align-items: center; gap: 16px;
          margin-bottom: 16px; flex-wrap: wrap;
        }
        .brand { display: flex; align-items: center; gap: 12px; }
        .brand svg { width: 34px; height: 34px; }
        .brand span { font-size: 22px; font-weight: 500; color: var(--primary-text-color); }
        .accounts {
          margin-left: auto; display: flex; flex-direction: column; gap: 8px;
          min-width: 260px; max-width: 420px;
        }
        .chip {
          display: flex; align-items: center; gap: 10px;
          background: var(--card-background-color, #fff);
          border-radius: 12px; padding: 8px 12px;
          box-shadow: var(--ha-card-box-shadow, 0 1px 3px rgba(0,0,0,.12));
          font-size: 13px; color: var(--primary-text-color); text-decoration: none;
        }
        .chip .who { display: flex; flex-direction: column; line-height: 1.35; min-width: 0; }
        .chip .who strong { font-weight: 500; }
        .chip .who span { font-size: 12px; }
        .chip .link { margin-left: auto; white-space: nowrap; }
        .card {
          background: var(--card-background-color, #fff);
          border-radius: var(--ha-card-border-radius, 12px);
          box-shadow: var(--ha-card-box-shadow, 0 1px 3px rgba(0,0,0,.12));
          margin-bottom: 16px; overflow: hidden;
        }
        .card h2 {
          font-size: 16px; font-weight: 500; margin: 0; padding: 16px;
          color: var(--primary-text-color);
          display: flex; align-items: center; justify-content: space-between; gap: 12px;
        }
        table { width: 100%; border-collapse: collapse; }
        th, td {
          padding: 12px 16px; text-align: left; font-size: 14px; font-weight: 400;
          color: var(--primary-text-color); border-top: 1px solid var(--divider-color, #e0e0e0);
        }
        th { font-size: 12px; color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: .04em; }
        tr:hover td { background: var(--secondary-background-color, rgba(0,0,0,.02)); }
        td.actions { text-align: right; white-space: nowrap; }
        .muted { color: var(--secondary-text-color); }
        .dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
        .ok { background: var(--success-color, #43a047); }
        .bad { background: var(--error-color, #db4437); }
        .empty { padding: 20px 16px; color: var(--secondary-text-color); font-size: 14px; }
        .error { padding: 16px; color: var(--error-color, #db4437); font-size: 14px; }
        button, a.button {
          font: inherit; font-size: 13px; cursor: pointer;
          border: none; border-radius: 999px; padding: 7px 14px;
          background: var(--primary-color); color: var(--text-primary-color, #fff);
          text-decoration: none; display: inline-block;
        }
        button.link, a.link {
          background: none; color: var(--primary-color); padding: 7px 8px;
        }
        button[disabled] { opacity: .6; cursor: default; }
      </style>
      <div class="wrap"><div id="body"></div></div>
    `;
    this.shadowRoot.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-booking]");
      if (button) this._cancel(button.dataset.entry, button.dataset.booking, button);
    });
  }

  _paint() {
    const t = this._t;
    const body = this.shadowRoot.getElementById("body");
    if (!this._data) {
      // An error before the first reply would otherwise hide behind a
      // loading message that never goes away.
      const message = this._error ? `<div class="error">${esc(this._error)}</div>`
        : `<div class="empty">${esc(t.loading)}</div>`;
      body.innerHTML = `<div class="card">${message}</div>`;
      return;
    }
    const { accounts, bookings, jobs } = this._data;
    body.innerHTML = `
      ${header(t, accounts)}
      ${this._error ? `<div class="card"><div class="error">${esc(this._error)}</div></div>` : ""}
      ${card(
        t.existingBookings,
        "",
        [t.when, t.court, t.account, ""],
        bookings.map((booking) => bookingRow(t, booking)),
        t.noBookings,
      )}
      ${card(
        t.bookingJobs,
        `<a class="button" href="${SETTINGS_URL}">${esc(t.manageJobs)}</a>`,
        [t.nextSlot, t.court, t.account, t.status],
        jobs.map((job) => jobRow(t, job)),
        t.noJobs,
      )}
    `;
  }
}

function header(t, accounts) {
  const chips = accounts.length
    ? accounts
        .map(
          (account) => `
          <div class="chip">
            <span class="dot ${account.authenticated ? "ok" : "bad"}"></span>
            <span class="who">
              <strong>${esc(account.title)}</strong>
              <span class="muted">${esc(account.venue || "")} · ${esc(
                account.authenticated ? t.authorised : t.signInProblem,
              )}</span>
            </span>
            <a class="link" href="${SETTINGS_URL}">${esc(t.manage)}</a>
          </div>`,
        )
        .join("")
    : `<a class="chip" href="${SETTINGS_URL}">${esc(t.noAccounts)}</a>`;
  return `
    <header>
      <div class="brand">
        <svg viewBox="0 0 708 708" aria-hidden="true">
          <path fill="var(--primary-color)" fill-rule="evenodd" clip-rule="evenodd" d="${LOGO}"/>
        </svg>
        <span>Skedda</span>
      </div>
      <div class="accounts">${chips}</div>
    </header>`;
}

function card(title, action, headings, rows, empty) {
  const head = `<tr>${headings.map((h) => `<th>${esc(h)}</th>`).join("")}</tr>`;
  const inner = rows.length
    ? `<table>${head}${rows.join("")}</table>`
    : `<div class="empty">${esc(empty)}</div>`;
  return `<div class="card"><h2>${esc(title)}${action}</h2>${inner}</div>`;
}

function bookingRow(t, booking) {
  return `<tr>
    <td>${when(booking.start)}</td>
    <td>${esc(booking.court)}</td>
    <td>${esc(booking.account)}</td>
    <td class="actions">
      <button class="link" data-entry="${esc(booking.entry_id)}"
              data-booking="${esc(booking.booking_id)}">${esc(t.cancel)}</button>
    </td>
  </tr>`;
}

function jobRow(t, job) {
  const repeat = job.repeat === "once" ? "" : ` <span class="muted">(${esc(t[job.repeat] || job.repeat)})</span>`;
  const status = esc(t[job.status] || job.status);
  const detail =
    job.status === "armed" && job.opens_at
      ? `${status} <span class="muted">— ${esc(t.opens)} ${when(job.opens_at)}</span>`
      : status;
  // No per-row link: it led to the same page as the button above it, and two
  // ways to the same place read as two different places.
  return `<tr>
    <td>${job.next_slot ? when(job.next_slot) : `<span class="muted">—</span>`}</td>
    <td>${esc(job.court)}${repeat}</td>
    <td>${esc(job.account)}</td>
    <td>${detail}</td>
  </tr>`;
}

function when(iso) {
  return esc(
    new Date(iso).toLocaleString(undefined, {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }),
  );
}

function esc(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}

customElements.define("skedda-panel", SkeddaPanel);
