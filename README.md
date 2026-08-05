<div align="center">

```
 ██╗       ███████╗ ████████╗ ██╗  ██╗ ███████╗
 ██║       ██╔════╝ ╚══██╔══╝ ██║  ██║ ██╔════╝
 ██║       █████╗      ██║    ███████║ █████╗  
 ██║       ██╔══╝      ██║    ██╔══██║ ██╔══╝  
 ███████╗  ███████╗    ██║    ██║  ██║ ███████╗
 ╚══════╝  ╚══════╝    ╚═╝    ╚═╝  ╚═╝ ╚══════╝
```

**`[ mailbox recon // GDPR art. 17 strike kit // response tracking ]`**

*Every service that ever mailed you knows who you are.
LETHE finds them all — and makes them forget.*

</div>

---

## What it does

Your mailbox is a map of your digital footprint: every "Welcome!", every
"Confirm your account", every newsletter is a company holding your data.
LETHE reads that map and burns it, in five moves:

| phase | command | what happens |
|---|---|---|
| 🔍 **recon** | `fpd scan` | reads every mailbox in your **Betterbird / Thunderbird** profile, catalogs every sender domain + address, detects registration signals, cross-references [JustDeleteMe](https://justdeleteme.xyz) |
| ⚖️ **triage** | `fpd review` | interactive: per domain you decide — **DELETE** (send erasure request), **KEEP**, or **MANUAL**. Excel round-trip available (`--csv-export` / `--csv`) |
| 📝 **arm** | `fpd generate` | writes a GDPR **Art. 17** erasure request (.eml, German or English, auto-detected) for every DELETE domain — and if no contact address is known, the built-in **mail sniffer** crawls the service's website for `privacy@` / `support@` / Impressum addresses |
| 🚀 **strike** | `fpd send` | sends everything via your SMTP account(s). One confirmation per mail — or `--yes` for full autosend. Every mail gets a tracked **Message-ID**, and Art. 12(3) starts the one-month clock |
| 📡 **track** | `fpd check` | re-scans your mailbox: **bounces** mark a contact address *dead* (and the domain gets re-drafted with the next-best address), **replies** mark it *alive* and the request *responded*. Overdue deadlines surface in `fpd status` with an escalation hint |

Everything lands in a local SQLite database — your decisions, every sent
request, every bounce, every reply. Nothing ever leaves your machine except
the erasure mails you decide to send.

### The contact knowledge base

Bounce tracking has a nice side effect: over time you build a database of
**verified-working (and verified-dead) privacy contact addresses** per
service. `fpd export-contacts` dumps it as CSV/JSON — with zero personal
data — so you can share it, e.g. with the JustDeleteMe project.

---

## Install

```bash
git clone https://github.com/Himerys/LETHE
cd Footprint_decimator
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install .
```

Then:

```bash
fpd                # first run: setup wizard + dashboard, tells you the next step
fpd scan           # inventory your mailbox
fpd review         # d / k / m — decide
fpd generate       # drafts + auto-sniff missing contacts
fpd send           # or: fpd send --yes
fpd check          # days later: bounces & replies
fpd status         # progress, deadlines, escalations
```

`fpd` with no arguments always tells you what to do next.

### Windows .exe

No Python needed — grab `fpd.exe` from the releases page, or build it yourself:

```bash
pip install . pyinstaller
pyinstaller fpd.spec           # -> dist/fpd.exe
```

(The GitHub Action in `.github/workflows/build-exe.yml` builds it on every
tagged release.)

---

## Configuration

`fpd configure` walks you through everything and writes `config.json` into
your working directory. **`config.json` is gitignored** — your name,
addresses and SMTP credentials stay local, always. `config.example.json`
documents every field.

| field | meaning |
|---|---|
| `sender_name`, `aliases` | how you sign the letters / other names your accounts use |
| `language` | `auto` (per-service German/English detection), `de`, `en` |
| `supervisory_authority` | optional: your DPA, named in the escalation paragraph |
| `accounts[]` | one SMTP account per mail identity you send from |
| `send_delay_seconds`, `send_daily_limit` | be nice to your mail provider (Gmail caps at ~500/day) |
| `sniffer.*` | crawler knobs: workers, max pages, locales, robots.txt |

Passwords can stay out of the file entirely: leave `password` empty and
you'll be prompted at send time (or set `FPD_SMTP_PASSWORD`).

---

## Commands

```
fpd                  dashboard + next step
fpd configure        setup wizard
fpd scan             inventory mailboxes        [--profile PATH] [-v]
fpd review           interactive triage         [--all] [--csv-export F] [--csv F]
fpd generate         write .eml drafts          [--no-sniff] [--prefer-self-service]
fpd sniff            hunt contact addresses     [targets...] [-i FILE] [-o CSV]
fpd send             SMTP send                  [--yes] [--limit N] [--dry-run]
fpd check            detect bounces & replies   [--profile PATH]
fpd status           progress, deadlines, overdue escalations
fpd sent DOMAIN...   mark as manually sent (starts the deadline)
fpd mark DOMAIN ST   deleted|responded|refused|escalated|noaccount
fpd contact D EMAIL  manually set a contact address
fpd export-contacts  shareable contact knowledge base (no personal data)
```

---

## Notes

- **Legal basis:** the letters invoke Art. 17 (erasure), Art. 7(3) (consent
  withdrawal), Art. 21 (objection), Art. 12(3) (one-month deadline) and
  Art. 19 (recipients) GDPR. This tool is not legal advice.
- **Sniffer etiquette:** the crawler respects `robots.txt`, uses a polite
  per-host delay and identifies itself honestly. You're looking up contact
  addresses to exercise your legal rights — behave accordingly.
- **Betterbird & Thunderbird** share the same profile format; both are
  auto-discovered (Windows/Linux/macOS/flatpak/snap). POP and IMAP folders
  are read directly from the local mbox files.
- **`fpd check`** reads your *local* mail — let Betterbird fetch new mail
  before running it.
- JustDeleteMe data (`footprint_decimator/data/sites.json`) courtesy of the
  [JustDeleteMe](https://github.com/jdorfman/justdelete.me) project (MIT).

---

<div align="center">

`decimate responsibly.`

</div>
