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
    locale: "en-GB",
    today: "Today",
    tomorrow: "Tomorrow",
    accounts: "Accounts",
    account: "Account",
    venue: "Venue",
    authorised: "authorised",
    signInProblem: "sign-in problem",
    manage: "Manage",
    manageJobs: "Manage",
    existingBookings: "Existing bookings",
    bookingJobs: "Booking jobs",
    job: "Job",
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
    watching: "Watching for freed slots",
    noWatches: "Nothing is being watched for.",
    rule: "Rule",
    daysHours: "Days and hours",
    watchState: "State",
    gateOpen: "watching",
    gateShut: "no quota left",
    ruleOff: "turned off",
    notifyOnly: "notify only",
    lastCatch: "last catch",
    every: "every",
    minutes: "min",
    until: "until",
    free: "Free",
    take: "Take",
    moveHere: "Move here",
    confirmMove: "Move this booking to",
    working: "…",
    runNow: "Run now",
    caught: "caught",
    nothingFree: "nothing free",
  },
  uk: {
    locale: "uk",
    today: "Сьогодні",
    tomorrow: "Завтра",
    accounts: "Акаунти",
    account: "Акаунт",
    venue: "Заклад",
    authorised: "авторизований",
    signInProblem: "помилка авторизації",
    manage: "Керувати",
    manageJobs: "Змінити",
    existingBookings: "Наявні бронювання",
    bookingJobs: "Завдання бронювання",
    job: "Завдання",
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
    watching: "Полювання за слотами",
    noWatches: "Ще немає жодного правила.",
    rule: "Правило",
    daysHours: "Дні й години",
    watchState: "Стан",
    gateOpen: "стежить",
    gateShut: "квота вичерпана",
    ruleOff: "вимкнено",
    notifyOnly: "лише сповіщення",
    lastCatch: "остання здобич",
    every: "кожні",
    minutes: "хв",
    until: "до",
    free: "Вільно",
    take: "Взяти",
    moveHere: "Перенести сюди",
    confirmMove: "Перенести це бронювання на",
    working: "…",
    runNow: "Запустити зараз",
    caught: "взяла",
    nothingFree: "нічого вільного",
  },
};

//: Two letters is all a row has space for, and the order matches
//: datetime.weekday(): Monday first.
const DAY_NAMES = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

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

  async _act(button, message) {
    // Every action changes the diary, so the reply is followed by a fresh look.
    button.disabled = true;
    button.textContent = this._t.working;
    let result = null;
    try {
      result = await this._hass.callWS(message);
      this._error = null;
    } catch (err) {
      this._error = err.message || String(err);
    }
    await this._refresh();
    return result;
  }

  _take(button) {
    const { entry, space, start, end } = button.dataset;
    return this._act(button, {
      type: "skedda_scheduler/take_slot",
      entry_id: entry,
      space_id: space,
      start,
      end,
    });
  }

  _move(button) {
    const { entry, booking, start, end } = button.dataset;
    if (!confirm(`${this._t.confirmMove} ${new Date(start).toLocaleString(this._t.locale)}?`)) {
      return null;
    }
    return this._act(button, {
      type: "skedda_scheduler/move_booking",
      entry_id: entry,
      booking_id: booking,
      start,
      end,
    });
  }

  async _run(button) {
    const { entry, rule } = button.dataset;
    const result = await this._act(button, {
      type: "skedda_scheduler/run_watch_rule",
      entry_id: entry,
      rule_id: rule,
    });
    if (result) {
      // Shown in the rule's row until the next press: what the press found.
      this._runs = { ...(this._runs || {}), [rule]: result.caught };
      this._paint();
    }
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
        td.time { width: 1%; white-space: nowrap; padding-left: 32px; }
        tr.day th {
          text-transform: none; letter-spacing: 0; font-size: 13px;
          background: var(--secondary-background-color, rgba(0,0,0,.03));
          padding: 8px 16px;
        }
        tr.day strong { color: var(--primary-text-color); font-weight: 600; }
        /* Two classes, so it outranks button.link further down. */
        button.link.icon { color: var(--secondary-text-color); font-size: 15px; line-height: 1; }
        button.link.icon:hover { color: var(--error-color, #db4437); }
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
        /* A slot nobody holds yet: there, but not ours - faded, with only its
           button at full strength. */
        tr.free td { color: var(--secondary-text-color); border-top-style: dashed; }
        tr.free td:not(.actions) { opacity: .55; }
        tr.free:hover td { background: none; }
        button.small { padding: 4px 12px; font-size: 12px; }
        button.ghost {
          background: none; color: var(--primary-color);
          border: 1px solid var(--primary-color); padding: 3px 11px;
        }
      </style>
      <div class="wrap"><div id="body"></div></div>
    `;
    this.shadowRoot.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-action]");
      if (!button) return;
      const action = button.dataset.action;
      if (action === "cancel") this._cancel(button.dataset.entry, button.dataset.booking, button);
      if (action === "take") this._take(button);
      if (action === "move") this._move(button);
      if (action === "run") this._run(button);
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
    const { accounts, bookings, jobs, watches, offers } = this._data;
    body.innerHTML = `
      ${header(t, accounts)}
      ${this._error ? `<div class="card"><div class="error">${esc(this._error)}</div></div>` : ""}
      ${card(
        t.existingBookings,
        "",
        null,
        bookingRows(t, bookings, offers || []),
        t.noBookings,
      )}
      ${card(
        t.bookingJobs,
        `<a class="button" href="${SETTINGS_URL}">${esc(t.manageJobs)}</a>`,
        [t.nextSlot, t.job, t.court, t.account, t.status],
        jobs.map((job) => jobRow(t, job)),
        t.noJobs,
      )}
      ${card(
        t.watching,
        "",
        [t.rule, t.daysHours, t.watchState, "", ""],
        (watches || []).map((watch) => watchRow(t, watch, this._runs || {})),
        t.noWatches,
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
  const head = headings ? `<tr>${headings.map((h) => `<th>${esc(h)}</th>`).join("")}</tr>` : "";
  const inner = rows.length
    ? `<table>${head}${rows.join("")}</table>`
    : `<div class="empty">${esc(empty)}</div>`;
  return `<div class="card"><h2>${esc(title)}${action}</h2>${inner}</div>`;
}

function bookingRows(t, bookings, offers) {
  // At a venue with one court the column is the same word on every row; it
  // earns its place only when it tells two bookings apart.
  const showCourt = new Set(bookings.map((booking) => booking.court)).size > 1;
  const columns = showCourt ? 4 : 3;
  const rows = [];
  let day = "";
  for (const booking of bookings) {
    const start = new Date(booking.start);
    if (start.toDateString() !== day) {
      day = start.toDateString();
      rows.push(dayRow(t, start, columns));
    }
    const nearby = offers.filter((offer) => offer.booking_id === booking.booking_id);
    const before = nearby.filter((offer) => new Date(offer.start) < new Date(booking.start));
    const after = nearby.filter((offer) => new Date(offer.start) >= new Date(booking.start));
    // Each free hour sits where it falls: the one before above, the one after
    // below, so the day reads as the court's own timeline.
    rows.push(...before.map((offer) => freeRow(t, offer, showCourt)));
    rows.push(bookingRow(t, booking, showCourt));
    rows.push(...after.map((offer) => freeRow(t, offer, showCourt)));
  }
  return rows;
}

function freeRow(t, offer, showCourt) {
  // Take is the quick one: a free hour goes to whoever asks first. Moving
  // gives up the hour we hold, so it asks first and looks quieter.
  const move = offer.kind === "move";
  const payer = move ? "" : ` <span>· ${esc(offer.account)}</span>`;
  return `<tr class="free">
    <td class="time">${clock(t, offer.start)}</td>
    ${showCourt ? "<td></td>" : ""}
    <td>${esc(t.free)}${payer}</td>
    <td class="actions">
      <button class="small ${move ? "ghost" : ""}" data-action="${move ? "move" : "take"}"
              data-entry="${esc(offer.entry_id)}" data-space="${esc(offer.space_id)}"
              data-booking="${esc(offer.booking_id)}"
              data-start="${esc(offer.start)}" data-end="${esc(offer.end)}"
              title="${esc(offer.account)}">${esc(move ? t.moveHere : t.take)}</button>
    </td>
  </tr>`;
}

function dayRow(t, start, columns) {
  const today = new Date();
  const tomorrow = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
  const relative =
    start.toDateString() === today.toDateString()
      ? t.today
      : start.toDateString() === tomorrow.toDateString()
        ? t.tomorrow
        : "";
  const weekday = start.toLocaleDateString(t.locale, { weekday: "short" });
  const date = `${pad(start.getDate())}.${pad(start.getMonth() + 1)}`;
  const suffix = relative ? ` · <strong>${esc(relative)}</strong>` : "";
  return `<tr class="day"><th colspan="${columns}">${esc(weekday)} ${date}${suffix}</th></tr>`;
}

function bookingRow(t, booking, showCourt) {
  return `<tr>
    <td class="time">${clock(t, booking.start)}</td>
    ${showCourt ? `<td>${esc(booking.court)}</td>` : ""}
    <td>${esc(booking.account)}</td>
    <td class="actions">
      <button class="link icon" data-action="cancel" data-entry="${esc(booking.entry_id)}"
              data-booking="${esc(booking.booking_id)}"
              title="${esc(t.cancel)}" aria-label="${esc(t.cancel)}">✕</button>
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
    <td>${esc(job.name)}</td>
    <td>${esc(job.court)}${repeat}</td>
    <td>${esc(job.account)}</td>
    <td>${detail}</td>
  </tr>`;
}

function watchRow(t, watch, runs) {
  const days = watch.days.map((day) => DAY_NAMES[day]).join(" ");
  const rate =
    watch.gate_open && watch.poll_interval_minutes
      ? ` <span class="muted">— ${esc(t.every)} ${watch.poll_interval_minutes} ${esc(t.minutes)}</span>`
      : "";
  // A shut gate is the ordinary resting state at a venue with a weekly
  // allowance, so it reads as a state rather than as a fault.
  const state = !watch.enabled ? t.ruleOff : watch.gate_open ? t.gateOpen : t.gateShut;
  // Without this a rule meant for one day and one meant for every week look
  // the same, and the difference only shows when it books the wrong week.
  const until = watch.until
    ? ` <span class="muted">${esc(t.until)} ${esc(
        new Date(`${watch.until}T00:00:00`).toLocaleDateString(t.locale),
      )}</span>`
    : "";
  const caught = watch.last_catch
    ? `<span class="muted">${esc(t.lastCatch)} ${when(watch.last_catch)}</span>`
    : "";
  const ran = watch.rule_id in runs
    ? `<span class="muted">${runs[watch.rule_id] ? `${esc(t.caught)} ${when(runs[watch.rule_id])}` : esc(t.nothingFree)}</span> `
    : "";
  const run = watch.enabled
    ? `<button class="small" data-action="run" data-entry="${esc(watch.entry_id)}"
               data-rule="${esc(watch.rule_id)}">▶ ${esc(t.runNow)}</button>`
    : "";
  return `<tr>
    <td>${esc(watch.name)}${watch.book ? "" : ` <span class="muted">(${esc(t.notifyOnly)})</span>`}</td>
    <td>${esc(days)} <span class="muted">${esc(watch.hours)}</span>${until}</td>
    <td>${esc(state)}${rate}</td>
    <td>${caught}</td>
    <td class="actions">${ran}${run}</td>
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

function clock(t, iso) {
  return esc(new Date(iso).toLocaleTimeString(t.locale, { hour: "2-digit", minute: "2-digit" }));
}

function pad(number) {
  return String(number).padStart(2, "0");
}

function esc(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}

customElements.define("skedda-panel", SkeddaPanel);
