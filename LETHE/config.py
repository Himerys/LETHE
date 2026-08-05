"""Configuration handling: config.json in the working directory + first-run wizard.

Personal data (name, addresses, SMTP credentials) lives ONLY in config.json,
which is gitignored. config.example.json documents every field.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from LETHE import ui

DEFAULT_SNIFFER = {
    "workers": 8,
    "max_pages": 15,
    "timeout": 10,
    "polite_delay": 0.5,
    "locales": ["en", "de"],
    "respect_robots": True,
    "guess_subdomains": False,
}


class Config:
    def __init__(self, path: Path, data: dict):
        self.path = path
        self.data = data

    # -- factory -------------------------------------------------------------

    @classmethod
    def load(cls, workdir: Path) -> "Config":
        path = workdir / "config.json"
        data = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                ui.err(f"config.json is unreadable ({e}) — fix or delete it.")
                raise SystemExit(1)
        return cls(path, data)

    def save(self) -> None:
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # -- accessors -----------------------------------------------------------

    @property
    def sender_name(self) -> str:
        return (self.data.get("sender_name") or "").strip()

    @property
    def aliases(self) -> list[str]:
        return [a for a in self.data.get("aliases", []) if a and a.strip()]

    @property
    def language(self) -> str:
        return self.data.get("language", "auto")

    @property
    def supervisory_authority(self) -> str:
        return (self.data.get("supervisory_authority") or "").strip()

    @property
    def profile_path(self) -> str:
        return (self.data.get("profile_path") or "").strip()

    @property
    def accounts(self) -> list[dict]:
        return [a for a in self.data.get("accounts", []) if a.get("email")]

    def account_for(self, addr: str) -> dict | None:
        addr = addr.lower()
        for a in self.accounts:
            if a["email"].lower() == addr:
                return a
        return None

    @property
    def send_delay(self) -> float:
        return float(self.data.get("send_delay_seconds", 5))

    @property
    def send_daily_limit(self) -> int:
        return int(self.data.get("send_daily_limit", 400))

    @property
    def sniffer(self) -> dict:
        merged = dict(DEFAULT_SNIFFER)
        merged.update(self.data.get("sniffer", {}))
        return merged

    def smtp_password(self, account: dict) -> str:
        """Password resolution: config value > env var > interactive prompt."""
        if account.get("password"):
            return account["password"]
        env = os.environ.get("FPD_SMTP_PASSWORD")
        if env:
            return env
        import getpass

        return getpass.getpass(
            f"SMTP password for {account.get('username') or account['email']}: "
        )


# ---------------------------------------------------------------------------
# First-run wizard
# ---------------------------------------------------------------------------

def wizard(workdir: Path) -> Config:
    from rich.prompt import Confirm, IntPrompt, Prompt

    c = ui.console
    cfg = Config.load(workdir)
    c.print("\n[head]═══ configuration ═══[/head]\n")
    c.print("[muted]Everything you enter stays in config.json (gitignored, local only).[/muted]\n")

    cfg.data["sender_name"] = Prompt.ask(
        "[accent]Full name for the letter signature[/accent]",
        default=cfg.sender_name or None,
    ).strip()

    aliases_raw = Prompt.ask(
        "[accent]Other names your accounts might use[/accent] [muted](comma-separated, empty = none)[/muted]",
        default=", ".join(cfg.aliases) if cfg.aliases else "",
        show_default=bool(cfg.aliases),
    )
    cfg.data["aliases"] = [a.strip() for a in aliases_raw.split(",") if a.strip()]

    cfg.data["language"] = Prompt.ask(
        "[accent]Letter language[/accent]",
        choices=["auto", "de", "en"],
        default=cfg.language,
    )

    cfg.data["supervisory_authority"] = Prompt.ask(
        "[accent]Your data protection authority[/accent] [muted](optional, e.g. 'LfDI Baden-Wuerttemberg')[/muted]",
        default=cfg.supervisory_authority,
        show_default=bool(cfg.supervisory_authority),
    ).strip()

    cfg.data["profile_path"] = Prompt.ask(
        "[accent]Thunderbird/Betterbird profile path[/accent] [muted](empty = auto-discover)[/muted]",
        default=cfg.profile_path,
        show_default=bool(cfg.profile_path),
    ).strip()

    accounts = list(cfg.accounts)
    if accounts:
        c.print(f"\n[muted]{len(accounts)} SMTP account(s) already configured: "
                + ", ".join(a["email"] for a in accounts) + "[/muted]")
    while Confirm.ask("\n[accent]Add an SMTP account for sending?[/accent]", default=not accounts):
        acc: dict = {}
        acc["email"] = Prompt.ask("  [accent]E-mail address[/accent]").strip().lower()
        acc["smtp_host"] = Prompt.ask("  [accent]SMTP host[/accent]").strip()
        acc["smtp_port"] = IntPrompt.ask("  [accent]SMTP port[/accent]", default=465)
        acc["security"] = Prompt.ask(
            "  [accent]Security[/accent]", choices=["ssl", "starttls", "none"], default="ssl"
        )
        acc["username"] = Prompt.ask("  [accent]Username[/accent]", default=acc["email"]).strip()
        if Confirm.ask(
            "  [accent]Store the password in config.json?[/accent] "
            "[muted](no = you'll be asked at send time)[/muted]",
            default=False,
        ):
            import getpass

            acc["password"] = getpass.getpass("  password (input hidden): ")
        else:
            acc["password"] = ""
        accounts = [a for a in accounts if a["email"] != acc["email"]] + [acc]
        ui.ok(f"account {acc['email']} saved")
    cfg.data["accounts"] = accounts

    cfg.data.setdefault("send_delay_seconds", 5)
    cfg.data.setdefault("send_daily_limit", 400)
    cfg.data.setdefault("sniffer", dict(DEFAULT_SNIFFER))

    cfg.save()
    ui.ok(f"configuration written to {cfg.path}")
    return cfg
