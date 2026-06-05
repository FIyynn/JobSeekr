"""
JobHuntrr â€” full local GUI: jobs tracker, profile/requirements builder, all CLI actions.

  python gui/jobhunter_gui.py
"""

import os
import csv
import re
import sys
import shutil
import subprocess
import threading
import webbrowser
import tkinter as tk
from pathlib import Path
from datetime import datetime
from tkinter import ttk, messagebox, scrolledtext, filedialog, simpledialog

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# Also ensure the gui package itself is importable regardless of cwd
_GUI_DIR = os.path.dirname(os.path.abspath(__file__))
if _GUI_DIR not in sys.path:
    sys.path.insert(0, _GUI_DIR)

from config.env_settings import bootstrap_settings
bootstrap_settings()

# Cooperative stop flag (shared with pipeline loops) â€” imported after path setup
from gui import stop_flag as _stop_flag

from storage.job_store import JobStore, DECISION_DISPLAY, GCC_LOCATION_KEYWORDS

DECISION_FILTERS = [
    ("Scored / actionable", None),
    ("All incl. pending score", "all_with_pending"),
    ("Pending score", "discovered"),
    ("Auto Apply", "auto_apply"),
    ("Manual Review", "manual_review"),
    ("Skipped", "skip"),
    ("Applied", "applied"),
    ("Closed", "closed"),
    ("Pending apply", "pending"),
    ("Off-target industry", "off_target"),
    ("Suggested alternate", "suggested_alternate"),
    ("Low salary", "low_salary"),
]

TAG_COLORS = {
    "Pending Score": "#8250df",
    "Auto Apply": "#1a7f37",
    "Manual Review": "#9a6700",
    "Skipped": "#cf222e",
    "Applied": "#0969da",
    "Closed": "#6e7781",
    "Excluded": "#656d76",
}


def _apply_method_display(job: dict) -> str:
    """Return a short, human-readable apply method string for the table."""
    from agents.apply_method import resolve_apply_method
    resolved = resolve_apply_method(job)
    return resolved or "â€”"


class JobHunterApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("JobHuntrr â€” UAE Job Agent")
        self.geometry("1280x820")
        self.minsize(1000, 600)
        self.store = JobStore()
        self._jobs: list[dict] = []
        self._selected_id = None
        self._console_log_handler = None
        self._bg_running = False  # True while a background task is active
        self._autonomous_process = None
        self._autonomous_refresh_after_id = None
        self._table_refresh_after_id = None
        self._table_revision = None
        # Column sort state: {col_key: bool}  True = ascending
        self._sort_col: str = "score"
        self._sort_asc: bool = False
        self._build_ui()
        self.reload_profile_settings_tab()
        self.reload_requirements_editor()
        self._update_resume_status_label()
        self.refresh_table()
        self._schedule_table_refresh()

    def _build_ui(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        self.jobs_tab = ttk.Frame(self.notebook)
        self.console_tab = ttk.Frame(self.notebook)
        self.linkedin_dm_tab = ttk.Frame(self.notebook)
        self.chat_tab = ttk.Frame(self.notebook)
        self.profile_tab = ttk.Frame(self.notebook)
        self.req_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.jobs_tab, text="Jobs")
        self.notebook.add(self.console_tab, text="Console")
        self.notebook.add(self.linkedin_dm_tab, text="LinkedIn DM")
        self.notebook.add(self.chat_tab, text="Chat")
        self.notebook.add(self.profile_tab, text="Profile Settings")
        self.notebook.add(self.req_tab, text="Requirements")

        self._chat_messages: list[dict] = []
        self._chat_busy = False

        self._build_jobs_tab()
        self._build_console_tab()
        self._build_linkedin_dm_tab()
        self._build_chat_tab()
        self._build_profile_tab()
        self._build_requirements_tab()

        self.log = scrolledtext.ScrolledText(
            self, height=4, wrap=tk.WORD, font=("Consolas", 9), state=tk.DISABLED
        )
        self.log.pack(fill=tk.X, padx=8, pady=(0, 8))

    # â”€â”€ Jobs tab â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _build_jobs_tab(self):
        top = ttk.Frame(self.jobs_tab, padding=6)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Filter:").pack(side=tk.LEFT, padx=(0, 4))
        self.filter_var = tk.StringVar(value="Scored / actionable")
        filt = ttk.Combobox(
            top, textvariable=self.filter_var, width=14, state="readonly",
            values=[f[0] for f in DECISION_FILTERS],
        )
        filt.pack(side=tk.LEFT, padx=4)
        filt.bind("<<ComboboxSelected>>", lambda e: self.refresh_table())

        self.gcc_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="GCC only", variable=self.gcc_var,
                        command=self.refresh_table).pack(side=tk.LEFT, padx=8)

        self.headless_var = tk.BooleanVar(value=True)

        self.validate_fit_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Validate fit before apply", variable=self.validate_fit_var).pack(
            side=tk.LEFT, padx=8
        )

        self.auto_enrich_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Auto-enrich profile", variable=self.auto_enrich_var).pack(
            side=tk.LEFT, padx=8
        )

        self.revisit_seen_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Revisit seen jobs", variable=self.revisit_seen_var).pack(
            side=tk.LEFT, padx=8
        )

        self.web_signal_search_var = tk.BooleanVar(
            value=os.getenv("WEB_SIGNAL_SEARCH", "1").strip().lower()
            not in ("0", "false", "no", "off")
        )
        ttk.Checkbutton(
            top,
            text="ATS feeds + career crawl + hidden posts",
            variable=self.web_signal_search_var,
        ).pack(side=tk.LEFT, padx=8)

        ttk.Label(top, text="Limit:").pack(side=tk.LEFT, padx=(8, 2))
        self.limit_var = tk.StringVar(value="0")
        ttk.Entry(top, textvariable=self.limit_var, width=5).pack(side=tk.LEFT)

        ttk.Label(top, text="Filter jobs:").pack(side=tk.LEFT, padx=(8, 4))
        self.search_var = tk.StringVar()
        se = ttk.Entry(top, textvariable=self.search_var, width=18)
        se.pack(side=tk.LEFT, padx=4)
        se.bind("<Return>", lambda e: self.refresh_table())
        ttk.Button(top, text="Filter", command=self.refresh_table).pack(side=tk.LEFT)

        self.stats_label = ttk.Label(top, text="")
        self.stats_label.pack(side=tk.RIGHT, padx=8)

        bar = ttk.LabelFrame(self.jobs_tab, text="Actions", padding=4)
        bar.pack(fill=tk.X, padx=6, pady=2)

        focus_row = ttk.Frame(bar)
        focus_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(focus_row, text="Run focus:").pack(side=tk.LEFT, padx=(2, 4))
        self.run_focus_var = tk.StringVar()
        focus_entry = ttk.Entry(focus_row, textvariable=self.run_focus_var)
        focus_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        focus_entry.bind("<Return>", lambda e: self.run_discovery(apply=False, live=False))
        ttk.Button(
            focus_row, text="Clear", command=lambda: self.run_focus_var.set("")
        ).pack(side=tk.LEFT, padx=2)
        ttk.Label(
            focus_row,
            text="Example: prioritize Lunate investment roles in Abu Dhabi",
            foreground="#6e7781",
        ).pack(side=tk.LEFT, padx=8)

        actions = [
            ("Refresh jobs", self.refresh_table),
            ("Search + apply now (LIVE)", self.run_autonomous_cycle),
            ("Start repeating search + apply (LIVE)", self.start_continuous_afk),
            ("Stop repeating runs", self.stop_continuous_afk),
            ("Apply queued jobs now (LIVE)", lambda: self.run_apply(dry=False)),
            ("Apply Easy Apply only (LIVE)", lambda: self.run_apply(dry=False, easy_apply_only=True)),
            ("Re-check LinkedIn apply methods", self.run_verify_apply_methods),
            ("Export Visible Jobs CSV", self.export_visible_jobs_csv),
            ("Search + score only", lambda: self.run_discovery(apply=False, live=False)),
            ("Score pending jobs", self.run_score_pending),
            ("Re-score all jobs", lambda: self.run_rescore(gcc=False, auto_only=False)),
            ("Test Easy Apply only (dry)", lambda: self.run_apply(dry=True, easy_apply_only=True)),
            ("Open selected job", self.open_url),
            ("Close browser windows", self.kill_browsers),
        ]
        row1 = ttk.Frame(bar)
        row1.pack(fill=tk.X)
        row2 = ttk.Frame(bar)
        row2.pack(fill=tk.X, pady=(4, 0))
        for i, (text, cmd) in enumerate(actions):
            parent = row1 if i < 5 else row2
            ttk.Button(parent, text=text, command=cmd).pack(side=tk.LEFT, padx=2, pady=2)
        self._stop_btn_jobs = tk.Button(
            row2, text="Stop current task", command=self._request_stop,
            bg="#c0392b", fg="white", activebackground="#922b21", activeforeground="white",
            font=("Segoe UI", 9, "bold"), relief=tk.FLAT, padx=8, pady=2, state=tk.DISABLED,
        )
        self._stop_btn_jobs.pack(side=tk.RIGHT, padx=(4, 2), pady=2)

        paned = ttk.PanedWindow(self.jobs_tab, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        table_frame = ttk.Frame(paned)
        paned.add(table_frame, weight=3)

        cols = (
            "score", "sps", "ips", "mode", "decision", "alt", "salary", "off_target",
            "company", "title", "location", "angle", "source", "method", "applied",
            "discovered",
        )
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="extended")
        spec = {
            "score": ("Score", 44),
            "sps": ("SPS", 40),
            "ips": ("IPS", 40),
            "mode": ("Mode", 72),
            "decision": ("Decision", 88),
            "alt": ("Alt?", 40),
            "salary": ("Salary", 72),
            "off_target": ("Off-tgt", 48),
            "company": ("Company", 100),
            "title": ("Role", 180),
            "location": ("Location", 90),
            "angle": ("Angle", 60),
            "source": ("Source", 56),
            "method": ("Method", 80),
            "applied": ("Applied", 48),
            "discovered": ("Discovered", 110),
        }
        for c, (label, w) in spec.items():
            self.tree.heading(c, text=label,
                              command=lambda col=c: self._sort_by(col))
            anchor = tk.W if c in ("company", "title", "location") else tk.CENTER
            self.tree.column(c, width=w, anchor=anchor)
        for disp, color in TAG_COLORS.items():
            self.tree.tag_configure(disp, foreground=color)

        vsb = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-1>", lambda e: self.open_url())

        detail_frame = ttk.LabelFrame(paned, text="Job details", padding=6)
        paned.add(detail_frame, weight=2)

        # â”€â”€ Bulk action bar (always visible) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        bulk_bar = ttk.Frame(detail_frame)
        bulk_bar.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(bulk_bar, text="Bulk action on selected:").pack(side=tk.LEFT, padx=(0, 4))
        self._bulk_action_var = tk.StringVar(value="â€” choose â€”")
        _bulk_choices = [
            "â€” choose â€”",
            "Mark applied",
            "Set: Auto Apply",
            "Set: Manual Review",
            "Set: Skip",
            "Delete selected",
        ]
        self._bulk_menu = ttk.OptionMenu(
            bulk_bar, self._bulk_action_var, _bulk_choices[0], *_bulk_choices
        )
        self._bulk_menu.pack(side=tk.LEFT, padx=2)
        tk.Button(
            bulk_bar, text="Run selected action", command=self._run_bulk_action,
            bg="#2471a3", fg="white", activebackground="#1a5276",
            activeforeground="white", relief=tk.FLAT, padx=8,
        ).pack(side=tk.LEFT, padx=4)

        self._sel_count_label = ttk.Label(bulk_bar, text="0 selected", foreground="#7f8c8d")
        self._sel_count_label.pack(side=tk.LEFT, padx=8)

        self.detail = scrolledtext.ScrolledText(
            detail_frame, height=12, wrap=tk.WORD, font=("Segoe UI", 10)
        )
        self.detail.pack(fill=tk.BOTH, expand=True)

        notes_row = ttk.Frame(detail_frame)
        notes_row.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(notes_row, text="Notes:").pack(side=tk.LEFT)
        self.notes_var = tk.StringVar()
        ttk.Entry(notes_row, textvariable=self.notes_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=6
        )
        ttk.Button(notes_row, text="Save notes", command=self.save_notes).pack(side=tk.LEFT)

        act = ttk.Frame(detail_frame)
        act.pack(fill=tk.X, pady=(6, 0))
        for text, cmd in [
            ("Mark as applied", self.mark_applied),
            ("Edit score", self.edit_score),
            ("Edit fit reason", self.edit_fit_reason),
            ("Set manual review", lambda: self.set_decision("manual_review")),
            ("Queue for auto-apply", lambda: self.set_decision("auto_apply")),
            ("Set skip", lambda: self.set_decision("skip")),
            ("Open selected job", self.open_url),
            ("Check profile gaps", self.check_gaps_for_job),
        ]:
            ttk.Button(act, text=text, command=cmd).pack(side=tk.LEFT, padx=3)
        # Delete â€” red, right-aligned
        tk.Button(
            act, text="Delete job", command=self.delete_job,
            bg="#c0392b", fg="white", activebackground="#922b21",
            activeforeground="white", relief=tk.FLAT, padx=8,
        ).pack(side=tk.RIGHT, padx=3)

    # â”€â”€ Signals tab (Hidden Opportunity Discovery) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _build_signals_tab(self, parent=None):
        from agents.hidden_opportunity_discovery import SIGNAL_STATUSES

        outer = ttk.Frame(parent or self.linkedin_dm_tab, padding=6)
        outer.pack(fill=tk.BOTH, expand=True)

        info = (
            "Hidden Opportunity Discovery â€” finds hiring signals in LinkedIn posts before they become job listings. "
            "Uses DuckDuckGo (free, no API key). Searches for 'DM me', 'send your CV', 'happy to refer', "
            "Emiratization campaigns, team expansions, and more."
        )
        ttk.Label(outer, text=info, wraplength=1100, foreground="#1a7f37").pack(anchor=tk.W, pady=(0, 4))

        # â”€â”€ Top controls â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        ctrl = ttk.LabelFrame(outer, text="Discovery controls", padding=6)
        ctrl.pack(fill=tk.X, pady=(0, 6))

        left_ctrl = ttk.Frame(ctrl)
        left_ctrl.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sig_comp_hdr = ttk.Frame(left_ctrl)
        sig_comp_hdr.pack(fill=tk.X)
        ttk.Label(sig_comp_hdr, text="Company targets (one per line):").pack(side=tk.LEFT)
        self._sig_comp_status = ttk.Label(sig_comp_hdr, text="", foreground="#7f8c8d")
        self._sig_comp_status.pack(side=tk.LEFT, padx=8)
        ttk.Button(sig_comp_hdr, text="â†º Refresh", command=self._refresh_signal_companies).pack(side=tk.RIGHT)
        self.sig_companies_box = scrolledtext.ScrolledText(
            left_ctrl, height=4, wrap=tk.WORD, font=("Consolas", 9)
        )
        self.sig_companies_box.pack(fill=tk.X, expand=True)
        self.after(150, self._refresh_signal_companies)

        right_ctrl = ttk.Frame(ctrl)
        right_ctrl.pack(side=tk.RIGHT, fill=tk.Y, padx=(12, 0))

        ttk.Label(right_ctrl, text="Max queries:").pack(anchor=tk.W)
        self.sig_max_queries_var = tk.StringVar(value="20")
        ttk.Entry(right_ctrl, textvariable=self.sig_max_queries_var, width=8).pack(anchor=tk.W)

        ttk.Label(right_ctrl, text="Results per query:").pack(anchor=tk.W, pady=(4, 0))
        self.sig_results_per_query_var = tk.StringVar(value="8")
        ttk.Entry(right_ctrl, textvariable=self.sig_results_per_query_var, width=8).pack(anchor=tk.W)

        ttk.Label(right_ctrl, text="Delay between queries (s):").pack(anchor=tk.W, pady=(4, 0))
        self.sig_delay_var = tk.StringVar(value="2.5")
        ttk.Entry(right_ctrl, textvariable=self.sig_delay_var, width=8).pack(anchor=tk.W)

        btn_row = ttk.Frame(right_ctrl)
        btn_row.pack(fill=tk.X, pady=(10, 0))
        tk.Button(
            btn_row, text="â–¶  Run Signal Search",
            command=self.run_signal_discovery,
            bg="#1a7f37", fg="white", activebackground="#116329", activeforeground="white",
            font=("Segoe UI", 9, "bold"), relief=tk.FLAT, padx=10, pady=4,
        ).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn_row, text="Reload", command=self.refresh_signals_table).pack(side=tk.LEFT, padx=2)

        # â”€â”€ Filter bar â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        fbar = ttk.Frame(outer)
        fbar.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(fbar, text="Filter status:").pack(side=tk.LEFT)
        self.sig_status_filter_var = tk.StringVar(value="All")
        ttk.Combobox(
            fbar, textvariable=self.sig_status_filter_var,
            values=["All"] + SIGNAL_STATUSES,
            state="readonly", width=20,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Label(fbar, text="Strength:").pack(side=tk.LEFT, padx=(8, 2))
        self.sig_strength_filter_var = tk.StringVar(value="All")
        ttk.Combobox(
            fbar, textvariable=self.sig_strength_filter_var,
            values=["All", "HIGH", "MEDIUM", "LOW"],
            state="readonly", width=10,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(fbar, text="Apply filter", command=self.refresh_signals_table).pack(side=tk.LEFT, padx=4)
        self.sig_count_label = ttk.Label(fbar, text="", foreground="#7f8c8d")
        self.sig_count_label.pack(side=tk.RIGHT, padx=8)

        # â”€â”€ Main paned area â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        paned = ttk.PanedWindow(outer, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True)

        table_frame = ttk.Frame(paned)
        paned.add(table_frame, weight=3)

        sig_cols = ("strength", "company", "person", "title", "role", "location",
                    "uae_nat", "score", "post_date", "status")
        self.sig_tree = ttk.Treeview(
            table_frame, columns=sig_cols, show="headings", selectmode="extended"
        )
        sig_spec = {
            "strength":  ("Signal",    72),
            "company":   ("Company",   130),
            "person":    ("Person",    140),
            "title":     ("Their Title", 160),
            "role":      ("Role",       110),
            "location":  ("Location",   80),
            "uae_nat":   ("UAEN",       46),
            "score":     ("Score",      48),
            "post_date": ("Posted",     88),
            "status":    ("Status",    150),
        }
        for col, (label, width) in sig_spec.items():
            self.sig_tree.heading(col, text=label)
            anchor = tk.W if col in ("company", "person", "title", "role") else tk.CENTER
            self.sig_tree.column(col, width=width, anchor=anchor)

        # Colour code by signal strength
        self.sig_tree.tag_configure("HIGH",   foreground="#1a7f37", font=("Segoe UI", 9, "bold"))
        self.sig_tree.tag_configure("MEDIUM", foreground="#9a6700")
        self.sig_tree.tag_configure("LOW",    foreground="#6e7781")

        sig_vsb = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.sig_tree.yview)
        self.sig_tree.configure(yscrollcommand=sig_vsb.set)
        self.sig_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sig_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.sig_tree.bind("<<TreeviewSelect>>", self.on_signal_select)
        self.sig_tree.bind("<Double-1>", lambda e: self.open_signal_url())

        # â”€â”€ Detail / action panel â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        detail_frame = ttk.LabelFrame(paned, text="Signal detail & outreach", padding=6)
        paned.add(detail_frame, weight=2)

        action_row = ttk.Frame(detail_frame)
        action_row.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(action_row, text="Status:").pack(side=tk.LEFT)
        self.sig_status_var = tk.StringVar(value="Not reviewed")
        ttk.Combobox(
            action_row, textvariable=self.sig_status_var,
            values=SIGNAL_STATUSES, state="readonly", width=22,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Label(action_row, text="Notes:").pack(side=tk.LEFT, padx=(8, 2))
        self.sig_notes_var = tk.StringVar()
        ttk.Entry(action_row, textvariable=self.sig_notes_var, width=40).pack(side=tk.LEFT, padx=4)
        ttk.Button(action_row, text="Save", command=self.save_signal_status).pack(side=tk.LEFT, padx=4)
        ttk.Button(action_row, text="Open post URL", command=self.open_signal_url).pack(side=tk.LEFT, padx=4)
        ttk.Button(action_row, text="Copy message", command=self.copy_signal_message).pack(side=tk.LEFT, padx=4)
        ttk.Button(action_row, text="Copy follow-up", command=self.copy_signal_followup).pack(side=tk.LEFT, padx=4)
        tk.Button(
            action_row, text="â†’ Push selected to Outreach",
            command=self.push_selected_signals_to_outreach,
            bg="#0969da", fg="white", activebackground="#1a3f6f",
            activeforeground="white", font=("Segoe UI", 9, "bold"),
            relief=tk.FLAT, padx=8,
        ).pack(side=tk.LEFT, padx=(8, 2))
        tk.Button(
            action_row, text="â†’ Push ALL HIGH+MEDIUM",
            command=self.push_all_signals_to_outreach,
            bg="#2471a3", fg="white", activebackground="#1a5276",
            activeforeground="white", relief=tk.FLAT, padx=6,
        ).pack(side=tk.LEFT, padx=2)
        tk.Button(
            action_row, text="Delete selected",
            command=self.delete_selected_signals,
            bg="#c0392b", fg="white", activebackground="#922b21",
            activeforeground="white", relief=tk.FLAT, padx=6,
        ).pack(side=tk.RIGHT, padx=4)

        # â”€â”€ Manual import â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        import_frame = ttk.LabelFrame(detail_frame, text="Manual import (paste post text / URL)", padding=4)
        import_frame.pack(fill=tk.X, pady=(4, 4))

        imp_fields = ttk.Frame(import_frame)
        imp_fields.pack(fill=tk.X, pady=(0, 2))
        for label, attr, width in [
            ("Company:", "sig_imp_company_var", 18),
            ("Person:", "sig_imp_person_var", 16),
            ("Their title:", "sig_imp_title_var", 20),
        ]:
            ttk.Label(imp_fields, text=label).pack(side=tk.LEFT)
            var = tk.StringVar()
            setattr(self, attr, var)
            ttk.Entry(imp_fields, textvariable=var, width=width).pack(side=tk.LEFT, padx=(0, 6))

        imp_urls = ttk.Frame(import_frame)
        imp_urls.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(imp_urls, text="Post URL:").pack(side=tk.LEFT)
        self.sig_imp_url_var = tk.StringVar()
        ttk.Entry(imp_urls, textvariable=self.sig_imp_url_var, width=38).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(imp_urls, text="LinkedIn /in/ URL:").pack(side=tk.LEFT)
        self.sig_imp_linkedin_var = tk.StringVar()
        e = ttk.Entry(imp_urls, textvariable=self.sig_imp_linkedin_var, width=38)
        e.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Label(imp_urls, text="â† required to send", foreground="#9a6700").pack(side=tk.LEFT)

        imp_text_row = ttk.Frame(import_frame)
        imp_text_row.pack(fill=tk.X)
        ttk.Label(imp_text_row, text="Post text:").pack(side=tk.LEFT, anchor=tk.N, pady=2)
        self.sig_imp_text = scrolledtext.ScrolledText(
            imp_text_row, height=3, wrap=tk.WORD, font=("Consolas", 9)
        )
        self.sig_imp_text.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 4))
        ttk.Button(
            imp_text_row, text="Import", command=self.import_signal_from_paste
        ).pack(side=tk.LEFT, anchor=tk.N, pady=2)

        # â”€â”€ Detail text â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        self.sig_detail = scrolledtext.ScrolledText(
            detail_frame, height=10, wrap=tk.WORD, font=("Consolas", 9)
        )
        self.sig_detail.pack(fill=tk.BOTH, expand=True)

        self._sig_rows: list[dict] = []
        self.refresh_signals_table()

    def refresh_signals_table(self, rows=None):
        if not hasattr(self, "sig_tree"):
            return
        if rows is None:
            from agents.hidden_opportunity_discovery import load_signals
            status_f = self.sig_status_filter_var.get() if hasattr(self, "sig_status_filter_var") else "All"
            rows = load_signals(status_filter=status_f if status_f != "All" else None)

        strength_f = self.sig_strength_filter_var.get() if hasattr(self, "sig_strength_filter_var") else "All"
        if strength_f != "All":
            rows = [r for r in rows if r.get("signal_strength") == strength_f]

        self._sig_rows = rows
        for item in self.sig_tree.get_children():
            self.sig_tree.delete(item)
        for row in rows:
            strength = row.get("signal_strength", "LOW")
            self.sig_tree.insert(
                "", tk.END,
                iid=row["id"],
                tags=(strength,),
                values=(
                    strength,
                    (row.get("company") or "")[:28],
                    (row.get("person") or "")[:28],
                    (row.get("title") or "")[:32],
                    (row.get("role_mentioned") or "")[:20],
                    (row.get("location_mentioned") or "")[:16],
                    "âœ“" if row.get("is_uae_national") else "",
                    row.get("relevance_score", 0),
                    (row.get("post_date") or "")[:10],
                    row.get("status", "Not reviewed"),
                ),
            )
        if hasattr(self, "sig_count_label"):
            self.sig_count_label.config(text=f"{len(rows)} signal(s)")

    def _selected_sig_row(self) -> dict:
        if not hasattr(self, "sig_tree"):
            return {}
        sel = self.sig_tree.selection()
        if not sel:
            return {}
        row_id = sel[0]
        for row in getattr(self, "_sig_rows", []):
            if row.get("id") == row_id:
                return row
        return {}

    def on_signal_select(self, _event=None):
        row = self._selected_sig_row()
        if not row:
            return
        self.sig_status_var.set(row.get("status") or "Not reviewed")
        self.sig_notes_var.set(row.get("notes") or "")
        lines = [
            f"{'='*60}",
            f"Signal: {row.get('signal_strength')}  |  Score: {row.get('relevance_score')}/100  |  UAE National: {'Yes' if row.get('is_uae_national') else 'No'}",
            f"Company:  {row.get('company') or '(unknown)'}",
            f"Person:   {row.get('person') or '(unknown)'}   â€”   {row.get('title') or '(title unknown)'}",
            f"Role:     {row.get('role_mentioned') or '(not specified)'}",
            f"Location: {row.get('location_mentioned') or '(not specified)'}",
            f"Post date: {row.get('post_date') or '(unknown)'}",
            f"Post URL: {row.get('post_url') or '(none)'}",
            f"Source:   {row.get('source', 'web_search')}",
            f"Discovered: {(row.get('discovered_at') or '')[:16]}",
            "",
            "Hiring language:",
            row.get("hiring_language") or "(none found)",
            "",
            "Call to action:",
            row.get("cta") or "(none found)",
            "",
            "Why relevant to Rashed:",
            row.get("why_relevant") or "",
            "",
            "â”€â”€â”€ Message to send â”€â”€â”€",
            row.get("message_to_send") or "(not generated)",
            "",
            "â”€â”€â”€ Follow-up after acceptance â”€â”€â”€",
            row.get("followup_message") or "(not generated)",
            "",
            "â”€â”€â”€ Raw snippet â”€â”€â”€",
            row.get("raw_snippet") or "",
            "",
            f"Status: {row.get('status')}   Notes: {row.get('notes') or 'â€”'}",
        ]
        self.sig_detail.delete("1.0", tk.END)
        self.sig_detail.insert(tk.END, "\n".join(lines))

    def save_signal_status(self):
        row = self._selected_sig_row()
        if not row:
            messagebox.showinfo("Signals", "Select a signal row first.")
            return
        from agents.hidden_opportunity_discovery import update_signal
        update_signal(
            row["id"],
            status=self.sig_status_var.get().strip() or "Not reviewed",
            notes=self.sig_notes_var.get().strip(),
        )
        self.refresh_signals_table()
        self.log_msg("Signal status saved")

    def open_signal_url(self):
        row = self._selected_sig_row()
        url = row.get("post_url") if row else ""
        if url:
            webbrowser.open(url)
        else:
            messagebox.showinfo("Signals", "No post URL for this signal.")

    def copy_signal_message(self):
        row = self._selected_sig_row()
        msg = row.get("message_to_send", "") if row else ""
        if not msg:
            messagebox.showinfo("Signals", "No message available for this signal.")
            return
        self.clipboard_clear()
        self.clipboard_append(msg)
        self.update()
        self.log_msg("Signal message copied to clipboard")

    def copy_signal_followup(self):
        row = self._selected_sig_row()
        msg = row.get("followup_message", "") if row else ""
        if not msg:
            messagebox.showinfo("Signals", "No follow-up message for this signal.")
            return
        self.clipboard_clear()
        self.clipboard_append(msg)
        self.update()
        self.log_msg("Signal follow-up copied to clipboard")

    def delete_selected_signals(self):
        if not hasattr(self, "sig_tree"):
            return
        sel = list(self.sig_tree.selection())
        if not sel:
            messagebox.showinfo("Signals", "Select one or more signals to delete.")
            return
        if not messagebox.askyesno("Signals", f"Delete {len(sel)} signal(s)?"):
            return
        from agents.hidden_opportunity_discovery import delete_signals
        delete_signals(sel)
        self.refresh_signals_table()
        if hasattr(self, "sig_detail"):
            self.sig_detail.delete("1.0", tk.END)
        self.log_msg(f"Deleted {len(sel)} signal(s)")

    def import_signal_from_paste(self):
        text = self.sig_imp_text.get("1.0", tk.END).strip()
        url = self.sig_imp_url_var.get().strip() if hasattr(self, "sig_imp_url_var") else ""
        company = self.sig_imp_company_var.get().strip() if hasattr(self, "sig_imp_company_var") else ""
        person = self.sig_imp_person_var.get().strip() if hasattr(self, "sig_imp_person_var") else ""
        person_title = self.sig_imp_title_var.get().strip() if hasattr(self, "sig_imp_title_var") else ""
        if not text and not url:
            messagebox.showinfo("Signals", "Paste post text or enter a URL first.")
            return
        linkedin_url = self.sig_imp_linkedin_var.get().strip() if hasattr(self, "sig_imp_linkedin_var") else ""
        from agents.hidden_opportunity_discovery import import_from_paste, update_signal
        signal = import_from_paste(
            text=text or url,
            url=url,
            company=company,
            person=person,
            person_title=person_title,
        )
        # Store the LinkedIn /in/ URL separately if provided
        if linkedin_url and signal.get("id"):
            update_signal(signal["id"], post_url=linkedin_url)
            signal["post_url"] = linkedin_url
        self.sig_imp_text.delete("1.0", tk.END)
        self.refresh_signals_table()
        sid = signal.get("id", "")
        if sid and sid in self.sig_tree.get_children():
            self.sig_tree.selection_set(sid)
            self.sig_tree.see(sid)
            self.on_signal_select()
        self.log_msg(
            f"Imported {signal.get('signal_strength')} signal "
            f"(score {signal.get('relevance_score')}) from {company or url or 'paste'}"
            + (" â€” LinkedIn URL saved" if linkedin_url else " â€” no LinkedIn URL yet")
        )

    def push_selected_signals_to_outreach(self):
        if not hasattr(self, "sig_tree"):
            return
        sel = list(self.sig_tree.selection())
        if not sel:
            messagebox.showinfo("Signals", "Select one or more signals to push.")
            return
        from agents.hidden_opportunity_discovery import push_signals_to_outreach
        pushed, skipped, warnings = push_signals_to_outreach(signal_ids=sel, skip_no_url=False)
        self.refresh_signals_table()
        self.refresh_linkedin_outreach()
        msg = f"Pushed {pushed} signal(s) to Outreach tab."
        if skipped:
            msg += f"\n{skipped} have no LinkedIn /in/ URL â€” add it before sending."
        if warnings:
            msg += "\n\n" + "\n".join(warnings[:6])
        messagebox.showinfo("Signals â†’ Outreach", msg)
        self.log_msg(f"[SIGNAL] Pushed {pushed} signal(s) to Outreach")
        # Switch to Outreach sub-tab so user sees the result
        if hasattr(self, "_linkedin_subnb"):
            self._linkedin_subnb.select(self._outreach_subtab)

    def push_all_signals_to_outreach(self):
        from agents.hidden_opportunity_discovery import load_signals, push_signals_to_outreach
        candidates = [
            s for s in load_signals()
            if s.get("signal_strength") in ("HIGH", "MEDIUM")
            and s.get("status") not in ("Archived", "No response", "Sent connection request",
                                         "Accepted", "Follow-up sent", "Replied")
        ]
        if not candidates:
            messagebox.showinfo("Signals", "No HIGH/MEDIUM signals to push (excluding already-sent).")
            return
        if not messagebox.askyesno(
            "Push to Outreach",
            f"Push {len(candidates)} HIGH/MEDIUM signal(s) to the Outreach tab?\n\n"
            "Signals without a LinkedIn /in/ URL will be flagged â€” add the URL before sending."
        ):
            return
        ids = [s["id"] for s in candidates]
        pushed, skipped, warnings = push_signals_to_outreach(signal_ids=ids, skip_no_url=False)
        self.refresh_signals_table()
        self.refresh_linkedin_outreach()
        msg = f"Pushed {pushed} signal(s) to Outreach tab."
        if skipped:
            msg += f"\n{skipped} are missing LinkedIn /in/ URLs â€” add before sending."
        messagebox.showinfo("Signals â†’ Outreach", msg)
        self.log_msg(f"[SIGNAL] Pushed {pushed} signal(s) to Outreach ({skipped} need LinkedIn URL)")
        if hasattr(self, "_linkedin_subnb"):
            self._linkedin_subnb.select(self._outreach_subtab)

    def run_signal_discovery(self):
        companies_text = self.sig_companies_box.get("1.0", tk.END).strip()
        extra_companies = [c.strip() for c in companies_text.splitlines() if c.strip()]
        try:
            max_q = max(1, min(50, int(self.sig_max_queries_var.get().strip() or "20")))
        except ValueError:
            max_q = 20
        try:
            per_q = max(1, min(20, int(self.sig_results_per_query_var.get().strip() or "8")))
        except ValueError:
            per_q = 8
        try:
            delay = max(0.5, float(self.sig_delay_var.get().strip() or "2.5"))
        except ValueError:
            delay = 2.5

        def task():
            from agents.hidden_opportunity_discovery import run_signal_discovery

            def progress(i, total, query):
                self.log_msg(f"[SIGNAL] Query {i+1}/{total}: {query[:70]}")
                self.after(0, self.refresh_signals_table)

            found = run_signal_discovery(
                extra_companies=extra_companies or None,
                max_queries=max_q,
                results_per_query=per_q,
                delay_seconds=delay,
                progress_callback=progress,
            )
            self.after(0, self.refresh_signals_table)
            self.log_msg(
                f"[SIGNAL] Done â€” {len(found)} new signal(s) discovered. "
                f"Check the Signals tab for results."
            )

        self._run_bg(
            "Hidden Signal Discovery",
            task,
            command_detail=f"queries={max_q}, per_query={per_q}, companies={len(extra_companies)}",
        )

    # â”€â”€ Profile Settings tab â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _build_profile_tab(self):
        top_bar = ttk.Frame(self.profile_tab, padding=6)
        top_bar.pack(fill=tk.X)
        ttk.Label(
            top_bar,
            text="All account settings saved to data/profile_settings.json (no .env file).",
        ).pack(side=tk.LEFT)
        ttk.Button(top_bar, text="Save all settings", command=self.save_all_profile_settings).pack(
            side=tk.RIGHT, padx=4
        )
        ttk.Button(top_bar, text="Reload", command=self.reload_profile_settings_tab).pack(
            side=tk.RIGHT, padx=4
        )

        canvas = tk.Canvas(self.profile_tab, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.profile_tab, orient=tk.VERTICAL, command=canvas.yview)
        self._profile_scroll_inner = ttk.Frame(canvas)
        self._profile_scroll_inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=self._profile_scroll_inner, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        outer = self._profile_scroll_inner

        # â”€â”€ Account & credentials â”€â”€
        acct = ttk.LabelFrame(outer, text="Account & credentials (saved to profile_settings.json)", padding=8)
        acct.pack(fill=tk.X, padx=8, pady=6)

        self.env_vars = {}
        env_fields = [
            ("APPLICANT_EMAIL", "Applicant email"),
            ("APPLICANT_PHONE", "Applicant phone (full, e.g. +971505612301)"),
            ("APPLICANT_PHONE_LOCAL", "Applicant phone (local, e.g. 0505612301)"),
            ("LINKEDIN_EMAIL", "LinkedIn login email"),
            ("LINKEDIN_PASSWORD", "LinkedIn password"),
            ("NOTION_TOKEN", "Notion token (optional)"),
            ("NOTION_DATABASE_ID", "Notion database ID (optional)"),
            ("STORAGE_BACKEND", "Storage (local or notion)"),
            ("AUTO_ENRICH_PROFILE", "Auto-enrich profile (1/0)"),
            ("PROFILE_DUAL_LAYER", "Dual layer: source + enhanced files (1/0)"),
            ("WEB_SIGNAL_SEARCH", "ATS feeds + career crawl + hidden posts (1/0)"),
            ("WEB_SIGNAL_MAX_RESULTS", "Max secondary-search signals per run"),
            ("WEB_SIGNAL_MAX_QUERIES", "Max role queries for web signal search"),
            ("WEB_SIGNAL_RESULTS_PER_QUERY", "Results per web signal query"),
            ("LINKEDIN_POST_SEARCH", "LinkedIn hiring-post search (1/0)"),
            ("LINKEDIN_POST_MAX_RESULTS", "LinkedIn posts per run"),
            ("GOOGLE_JOBS_SEARCH", "Google Jobs source (1/0)"),
            ("BROWSER_GOOGLE_SEARCH", "Browser Google indexed search (1/0)"),
            ("SERPAPI_API_KEY", "SerpApi key â€” optional, upgrades hidden-post search"),
            ("GOOGLE_SEARCH_API_KEY", "Google Search API key â€” optional, not recommended"),
            ("GOOGLE_SEARCH_CX", "Google Search engine ID (optional)"),
        ]
        for key, label in env_fields:
            row = ttk.Frame(acct)
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=label, width=22).pack(side=tk.LEFT)
            show = "*" if "PASSWORD" in key or key.endswith("_API_KEY") else None
            self.env_vars[key] = tk.StringVar()
            ttk.Entry(row, textvariable=self.env_vars[key], width=52, show=show).pack(
                side=tk.LEFT, fill=tk.X, expand=True, padx=4
            )

        # â”€â”€ Sign up & application defaults â”€â”€
        signup_fr = ttk.LabelFrame(
            outer,
            text="Sign up & application defaults (for ATS registration / job forms)",
            padding=8,
        )
        signup_fr.pack(fill=tk.X, padx=8, pady=6)
        ttk.Label(
            signup_fr,
            text="Used when applying to portals that ask you to create an account or fill identity fields. "
            "During apply, login walls are filled automatically from these values (email + password required). "
            "The password and learned per-portal credentials are protected with Windows encryption. "
            "Apply runs are AFK: blocked verification pages are deferred without opening dialogs. "
            "Email defaults to Applicant email above if left blank. Full name and location are auto-filled on save.",
            wraplength=720,
        ).pack(anchor=tk.W, pady=(0, 6))

        self.signup_vars = {}
        signup_fields = [
            ("first_name", "First name"),
            ("last_name", "Last name"),
            ("middle_name", "Middle name"),
            ("full_name", "Full name (auto if blank)"),
            ("gender", "Gender"),
            ("email", "Email (signup forms)"),
            ("password", "Password (portal signup)"),
            ("address", "Street address"),
            ("city", "City"),
            ("state", "State / region"),
            ("country", "Country"),
            ("postal_code", "Postal / ZIP code"),
            ("location", "Location (auto if blank)"),
            ("nationality", "Nationality"),
            ("date_of_birth", "Date of birth (YYYY-MM-DD)"),
        ]
        for key, label in signup_fields:
            row = ttk.Frame(signup_fr)
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=label, width=22).pack(side=tk.LEFT)
            show = "*" if key == "password" else None
            self.signup_vars[key] = tk.StringVar()
            ttk.Entry(row, textvariable=self.signup_vars[key], width=52, show=show).pack(
                side=tk.LEFT, fill=tk.X, expand=True, padx=4
            )

        # â”€â”€ Notion sync â”€â”€
        notion_fr = ttk.LabelFrame(
            outer,
            text="Notion sync (optional â€” local DB stays primary unless STORAGE_BACKEND=notion)",
            padding=8,
        )
        notion_fr.pack(fill=tk.X, padx=8, pady=6)
        ttk.Label(
            notion_fr,
            text="Pull: import Notion rows â†’ data/jobs.db.  Push: export local jobs â†’ Notion (create or update by URL).",
            wraplength=700,
        ).pack(anchor=tk.W)
        self.notion_sync_gcc_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(notion_fr, text="GCC only", variable=self.notion_sync_gcc_var).pack(
            anchor=tk.W, pady=(4, 0)
        )
        notion_btns = ttk.Frame(notion_fr)
        notion_btns.pack(fill=tk.X, pady=6)
        ttk.Button(
            notion_btns, text="Pull from Notion â†’ local",
            command=self.pull_from_notion,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            notion_btns, text="Push local â†’ Notion",
            command=self.push_to_notion,
        ).pack(side=tk.LEFT, padx=4)

        # â”€â”€ Links â”€â”€
        links = ttk.LabelFrame(outer, text="Links (LinkedIn URL required)", padding=8)
        links.pack(fill=tk.X, padx=8, pady=6)

        self.link_vars = {k: tk.StringVar() for k in ("linkedin", "github", "website", "other")}
        for key, label in [
            ("linkedin", "LinkedIn profile URL *"),
            ("github", "GitHub URL"),
            ("website", "Website URL"),
            ("other", "Other URL"),
        ]:
            row = ttk.Frame(links)
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=label, width=22).pack(side=tk.LEFT)
            ttk.Entry(row, textvariable=self.link_vars[key]).pack(
                side=tk.LEFT, fill=tk.X, expand=True, padx=4
            )

        self._extra_links_frame = ttk.Frame(links)
        self._extra_links_frame.pack(fill=tk.X, pady=4)
        self._extra_link_rows: list[tuple[tk.StringVar, tk.StringVar, ttk.Frame]] = []
        ttk.Button(links, text="+ Add another link", command=self._add_extra_link_row).pack(
            anchor=tk.W, pady=4
        )

        # â”€â”€ Resume (used by Apply on all ATS sites) â”€â”€
        resume_fr = ttk.LabelFrame(
            outer,
            text="Resume for applications (Workday / Greenhouse / file uploads)",
            padding=8,
        )
        resume_fr.pack(fill=tk.X, padx=8, pady=6)
        ttk.Label(
            resume_fr,
            text="This PDF is attached on apply. Save settings after changing. "
            "Upload copies into data/resume/ for a stable path.",
            wraplength=720,
        ).pack(anchor=tk.W)
        resume_row = ttk.Frame(resume_fr)
        resume_row.pack(fill=tk.X, pady=4)
        self.resume_path_var = tk.StringVar(value=os.path.join(ROOT, "Rashed_Alneyadi_Resume.pdf"))
        ttk.Entry(resume_row, textvariable=self.resume_path_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=4
        )
        ttk.Button(resume_row, text="Browseâ€¦", command=self._browse_resume).pack(side=tk.LEFT, padx=2)
        ttk.Button(resume_row, text="Upload PDFâ€¦", command=self._upload_resume_pdf).pack(
            side=tk.LEFT, padx=2
        )
        self.resume_status_var = tk.StringVar(value="")
        ttk.Label(resume_fr, textvariable=self.resume_status_var, foreground="#656d76").pack(
            anchor=tk.W, pady=(4, 0)
        )

        ttk.Label(resume_fr, text="Cover letter (optional â€” leave blank to auto-generate):").pack(anchor=tk.W, pady=(8, 0))
        cl_row = ttk.Frame(resume_fr)
        cl_row.pack(fill=tk.X, pady=4)
        self.cover_letter_path_var = tk.StringVar(value="")
        ttk.Entry(cl_row, textvariable=self.cover_letter_path_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=4
        )
        ttk.Button(cl_row, text="Browseâ€¦", command=self._browse_cover_letter).pack(side=tk.LEFT, padx=2)

        # â”€â”€ Profile enrich â”€â”€
        enrich_fr = ttk.LabelFrame(outer, text="Profile enrichment", padding=8)
        enrich_fr.pack(fill=tk.X, padx=8, pady=6)
        ttk.Label(
            enrich_fr,
            text="Dual layer (default ON): paste your master content in Source below; "
            "Enrich writes data/enhanced/ only. Scoring uses both.",
            wraplength=720,
        ).pack(anchor=tk.W)
        from config.md_loader import use_dual_layer

        self.safe_enrich_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            enrich_fr,
            text="Legacy mode: append enrich into source profile (uncheck for dual layer â†’ data/enhanced/)",
            variable=self.safe_enrich_var,
        ).pack(anchor=tk.W)
        if not use_dual_layer():
            self.safe_enrich_var.set(True)
        btn_row = ttk.Frame(enrich_fr)
        btn_row.pack(fill=tk.X, pady=(6, 0))
        for text, cmd in [
            ("Enrich from links + resume", self.enrich_profile),
            ("Parse resume only (append)", self.parse_resume_only),
            ("Restore latest backup", self.restore_profile_backup),
            ("Reload enhanced layers", self.reload_enhanced_editors),
        ]:
            ttk.Button(btn_row, text=text, command=cmd).pack(side=tk.LEFT, padx=4)

        # â”€â”€ Profile markdown (source) â”€â”€
        md_fr = ttk.LabelFrame(
            outer,
            text="Source profile â€” applicant_profile.md (your content; authoritative)",
            padding=8,
        )
        md_fr.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
        self.profile_editor = scrolledtext.ScrolledText(
            md_fr, height=14, wrap=tk.WORD, font=("Consolas", 10)
        )
        self.profile_editor.pack(fill=tk.BOTH, expand=True)
        prof_btns = ttk.Frame(md_fr)
        prof_btns.pack(fill=tk.X, pady=6)
        ttk.Button(prof_btns, text="Save profile text only", command=self.save_profile_editor).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(prof_btns, text="Fill gaps (manual skills)", command=self.fill_gaps_manual).pack(
            side=tk.LEFT, padx=4
        )

        enh_fr = ttk.LabelFrame(
            outer,
            text="Enhanced profile â€” data/enhanced/applicant_profile_enhanced.md (auto)",
            padding=8,
        )
        enh_fr.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
        self.profile_enhanced_editor = scrolledtext.ScrolledText(
            enh_fr, height=8, wrap=tk.WORD, font=("Consolas", 10)
        )
        self.profile_enhanced_editor.pack(fill=tk.BOTH, expand=True)
        enh_btns = ttk.Frame(enh_fr)
        enh_btns.pack(fill=tk.X, pady=4)
        ttk.Button(enh_btns, text="Save enhanced profile", command=self.save_profile_enhanced_editor).pack(
            side=tk.LEFT, padx=4
        )

    def _add_extra_link_row(self, name: str = "", url: str = ""):
        row = ttk.Frame(self._extra_links_frame)
        row.pack(fill=tk.X, pady=2)
        name_var = tk.StringVar(value=name)
        url_var = tk.StringVar(value=url)
        ttk.Label(row, text="Label", width=8).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=name_var, width=14).pack(side=tk.LEFT, padx=2)
        ttk.Label(row, text="URL", width=4).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=url_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        ttk.Button(
            row, text="Remove", width=8,
            command=lambda r=row: self._remove_extra_link_row(r),
        ).pack(side=tk.LEFT, padx=2)
        self._extra_link_rows.append((name_var, url_var, row))

    def _remove_extra_link_row(self, row_frame: ttk.Frame):
        self._extra_link_rows = [
            t for t in self._extra_link_rows if t[2] != row_frame
        ]
        row_frame.destroy()

    def _collect_links_from_gui(self) -> dict:
        links = {k: v.get().strip() for k, v in self.link_vars.items()}
        for name_var, url_var, _ in self._extra_link_rows:
            name, url = name_var.get().strip(), url_var.get().strip()
            if name and url:
                links[name] = url
        return links

    def reload_profile_settings_tab(self):
        from config.env_settings import load_all_settings, ENV_KEYS, SIGNUP_DEFAULTS_KEYS
        from agents.profile_manager import load_profile_body, CORE_LINK_KEYS

        data = load_all_settings()
        for key in ENV_KEYS:
            if key in self.env_vars:
                self.env_vars[key].set(data["env"].get(key, ""))

        signup = data["profile"].get("signup_defaults") or {}
        for key in SIGNUP_DEFAULTS_KEYS:
            if key in self.signup_vars:
                self.signup_vars[key].set(signup.get(key, ""))

        prof = data["profile"]
        for k in CORE_LINK_KEYS:
            if k in self.link_vars:
                self.link_vars[k].set(prof.get(k, ""))
        self.resume_path_var.set(prof.get("resume_path") or self.resume_path_var.get())
        self.cover_letter_path_var.set(prof.get("cover_letter_path") or "")

        for _, _, frame in list(self._extra_link_rows):
            self._remove_extra_link_row(frame)
        extra = {
            k: v for k, v in prof.items()
            if k not in CORE_LINK_KEYS and k not in ("resume_path", "extra_links")
            and v and str(v).startswith("http")
        }
        if prof.get("extra_links"):
            extra.update(prof["extra_links"])
        for name, url in extra.items():
            self._add_extra_link_row(name, url)

        self.profile_editor.delete("1.0", tk.END)
        self.profile_editor.insert(tk.END, load_profile_body())
        self.reload_enhanced_editors()
        self._update_resume_status_label()
        self.log_msg("Profile settings reloaded")

    def save_all_profile_settings(self, silent: bool = False) -> bool:
        from config.env_settings import (
            save_all_settings, apply_settings_to_runtime, ENV_KEYS, SIGNUP_DEFAULTS_KEYS,
        )
        from agents.profile_manager import save_links, validate_linkedin_required

        env = {k: self.env_vars[k].get().strip() for k in ENV_KEYS if k in self.env_vars}
        signup = {
            k: self.signup_vars[k].get().strip()
            for k in SIGNUP_DEFAULTS_KEYS
            if k in self.signup_vars
        }
        links = self._collect_links_from_gui()
        ok, err = validate_linkedin_required(links)
        if not ok:
            if not silent:
                messagebox.showerror("LinkedIn required", err)
            return False

        resume = self.resume_path_var.get().strip()
        cover_letter = self.cover_letter_path_var.get().strip()
        extra = {
            name: links[name]
            for name in links
            if name not in ("linkedin", "github", "website", "other")
        }
        save_all_settings(env, links, resume, extra_links=extra, signup_defaults=signup,
                          cover_letter_path=cover_letter)
        save_links(links)
        apply_settings_to_runtime(
            env, {
                **links,
                "resume_path": resume,
                "cover_letter_path": cover_letter,
                "signup_defaults": signup,
            }
        )
        self._update_resume_status_label()
        self.log_msg("Saved profile_settings.json")
        if not silent:
            messagebox.showinfo(
                "JobHuntrr",
                "All settings saved to data/profile_settings.json.\nActive for this session.",
            )
        return True

    # â”€â”€ Requirements tab â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _build_requirements_tab(self):
        outer = ttk.Frame(self.req_tab, padding=8)
        outer.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            outer,
            text="Requirements file drives scoring thresholds, search, and fit rules.",
        ).pack(anchor=tk.W)

        req_nb = ttk.Notebook(outer)
        req_nb.pack(fill=tk.BOTH, expand=True, pady=6)

        general = ttk.Frame(req_nb, padding=4)
        scoring = ttk.Frame(req_nb, padding=4)
        search = ttk.Frame(req_nb, padding=4)
        enhanced = ttk.Frame(req_nb, padding=4)
        builder = ttk.Frame(req_nb, padding=4)
        req_nb.add(general, text="Source requirements")
        req_nb.add(scoring, text="Scoring prompt")
        req_nb.add(search, text="Search")
        req_nb.add(enhanced, text="Enhanced (auto)")
        req_nb.add(builder, text="Copy prompt")

        ttk.Label(
            general,
            text="Source â€” applicant_requirements.md: geography, targets, skips, YAML thresholds.",
        ).pack(anchor=tk.W)
        self.req_editor = scrolledtext.ScrolledText(general, height=16, wrap=tk.WORD, font=("Consolas", 10))
        self.req_editor.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            scoring,
            text="Extra instructions for the AI scorer (industry focus, priorities, deal-breakers).",
        ).pack(anchor=tk.W)
        self.req_scoring_prompt = scrolledtext.ScrolledText(
            scoring, height=14, wrap=tk.WORD, font=("Consolas", 10)
        )
        self.req_scoring_prompt.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            search,
            text="Search queries (one per line: title keywords | location). Example: data scientist | Dubai",
        ).pack(anchor=tk.W)
        self.req_search_queries = scrolledtext.ScrolledText(
            search, height=8, wrap=tk.WORD, font=("Consolas", 10)
        )
        self.req_search_queries.pack(fill=tk.BOTH, expand=True, pady=4)
        ttk.Label(
            search,
            text="OR natural-language search prompt (used if queries box is empty):",
        ).pack(anchor=tk.W)
        self.req_search_prompt = scrolledtext.ScrolledText(
            search, height=6, wrap=tk.WORD, font=("Consolas", 10)
        )
        self.req_search_prompt.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            enhanced,
            text="Auto-generated supplements (Enrich / pipeline). Edit if needed.",
        ).pack(anchor=tk.W)
        self.req_enhanced_editor = scrolledtext.ScrolledText(
            enhanced, height=12, wrap=tk.WORD, font=("Consolas", 10)
        )
        self.req_enhanced_editor.pack(fill=tk.BOTH, expand=True, pady=4)
        ttk.Button(
            enhanced, text="Save enhanced requirements", command=self.save_requirements_enhanced_editor
        ).pack(anchor=tk.W)

        self._build_requirements_prompt_builder(builder)

        row = ttk.Frame(outer)
        row.pack(fill=tk.X)
        ttk.Button(row, text="Reload all", command=self.reload_requirements_editor).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(row, text="Save all", command=self.save_requirements_editor).pack(
            side=tk.LEFT, padx=4
        )

    def _build_requirements_prompt_builder(self, parent):
        """Read-only master prompt â€” user copies it into ChatGPT/Claude/etc."""
        from config.requirements_prompts import get_master_prompt

        ttk.Label(
            parent,
            text=(
                "Master prompt for building your Profile + Requirements with ChatGPT, "
                "Claude, Gemini, or any external LLM. Read-only â€” click Copy then paste "
                "into your chat. Replace the [PASTE â€¦] sections with your data."
            ),
            wraplength=900,
        ).pack(anchor=tk.W)

        ctl = ttk.Frame(parent)
        ctl.pack(fill=tk.X, pady=6)
        ttk.Button(ctl, text="Copy prompt", command=self._copy_requirements_prompt).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(
            ctl, text="Send to Chat tab", command=self._send_requirements_prompt_to_chat
        ).pack(side=tk.LEFT, padx=4)
        self._req_prompt_status = ttk.Label(ctl, text="", foreground="#1a7f37")
        self._req_prompt_status.pack(side=tk.LEFT, padx=10)

        self._req_prompt_preview = scrolledtext.ScrolledText(
            parent, wrap=tk.WORD, font=("Consolas", 10),
        )
        self._req_prompt_preview.pack(fill=tk.BOTH, expand=True, pady=4)
        self._req_prompt_text = get_master_prompt()
        self._req_prompt_preview.insert(tk.END, self._req_prompt_text)
        # Read-only â€” selection + copy keystrokes still work, edits do not.
        self._req_prompt_preview.configure(state=tk.DISABLED)
        self._req_prompt_preview.bind("<Key>", lambda e: "break")
        self._req_prompt_preview.bind("<Button-2>", lambda e: "break")
        self._req_prompt_preview.bind("<<Paste>>", lambda e: "break")
        self._req_prompt_preview.bind("<<Cut>>",   lambda e: "break")

    def _copy_requirements_prompt(self):
        text = getattr(self, "_req_prompt_text", "") or ""
        if not text:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update()
            if hasattr(self, "_req_prompt_status"):
                self._req_prompt_status.configure(text="Copied to clipboard")
                self.after(2500, lambda: self._req_prompt_status.configure(text=""))
            self.log_msg("Master prompt copied to clipboard")
        except Exception as e:
            messagebox.showerror("Copy failed", str(e))

    def _send_requirements_prompt_to_chat(self):
        text = getattr(self, "_req_prompt_text", "") or ""
        if not text or not hasattr(self, "chat_input"):
            return
        self.notebook.select(self.chat_tab)
        self.chat_input.delete("1.0", tk.END)
        self.chat_input.insert(tk.END, text)
        self.log_msg("Master prompt loaded into Chat input")

    # â”€â”€ Chat tab (direct Ollama conversation) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _build_chat_tab(self):
        from config.config import OLLAMA_BASE_URL, get_ollama_model

        chat_model = get_ollama_model()
        from config.apply_agent_rules import get_resume_path

        top = ttk.Frame(self.chat_tab, padding=6)
        top.pack(fill=tk.X)
        ttk.Label(
            top,
            text=f"Local model ({chat_model}). Chat does not apply to jobs - use Jobs > Apply queued jobs now (LIVE). "
            f"Resume for apply: {get_resume_path()}",
            wraplength=900,
        ).pack(side=tk.LEFT)

        saved_fr = ttk.LabelFrame(self.chat_tab, text="Chat saved prompts (used by Chat + Apply)", padding=6)
        saved_fr.pack(fill=tk.X, padx=6, pady=(0, 4))
        ttk.Label(
            saved_fr,
            text="Save instructions with /remember â€¦ or phrases like â€œavoid mentioning ADIAâ€. "
            "Stored in data/chat_saved_prompts.md",
            wraplength=880,
        ).pack(anchor=tk.W)
        self.chat_saved_prompts_box = scrolledtext.ScrolledText(
            saved_fr, height=5, wrap=tk.WORD, font=("Consolas", 9)
        )
        self.chat_saved_prompts_box.pack(fill=tk.X, pady=4)
        saved_btns = ttk.Frame(saved_fr)
        saved_btns.pack(fill=tk.X)
        ttk.Button(saved_btns, text="Reload", command=self._reload_chat_saved_prompts_ui).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(saved_btns, text="Save edits", command=self._save_chat_saved_prompts_from_ui).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(saved_btns, text="Clear all", command=self._clear_chat_saved_prompts).pack(
            side=tk.LEFT, padx=2
        )
        self.chat_auto_save_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            saved_btns, text="Auto-save instructions from chat", variable=self.chat_auto_save_var
        ).pack(side=tk.LEFT, padx=8)

        opts = ttk.Frame(self.chat_tab, padding=(6, 0))
        opts.pack(fill=tk.X)
        self.chat_include_profile = tk.BooleanVar(value=True)
        self.chat_include_requirements = tk.BooleanVar(value=False)
        self.chat_include_job = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opts, text="Include profile", variable=self.chat_include_profile
        ).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(
            opts, text="Include requirements", variable=self.chat_include_requirements
        ).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(
            opts, text="Include selected job", variable=self.chat_include_job
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(opts, text="Clear chat", command=self._chat_clear).pack(side=tk.RIGHT, padx=4)

        self.chat_display = scrolledtext.ScrolledText(
            self.chat_tab,
            wrap=tk.WORD,
            font=("Segoe UI", 10),
            state=tk.DISABLED,
        )
        self.chat_display.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.chat_display.tag_configure("user", foreground="#0969da", font=("Segoe UI", 10, "bold"))
        self.chat_display.tag_configure("assistant", foreground="#1a7f37")
        self.chat_display.tag_configure("system", foreground="#656d76", font=("Segoe UI", 9, "italic"))
        self.chat_display.tag_configure("error", foreground="#cf222e")

        input_fr = ttk.Frame(self.chat_tab, padding=6)
        input_fr.pack(fill=tk.X)
        self.chat_input = scrolledtext.ScrolledText(
            input_fr, height=3, wrap=tk.WORD, font=("Segoe UI", 10)
        )
        self.chat_input.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))
        self.chat_input.bind("<Control-Return>", lambda e: self._chat_send())
        btn_col = ttk.Frame(input_fr)
        btn_col.pack(side=tk.RIGHT, fill=tk.Y)
        self.chat_send_btn = ttk.Button(btn_col, text="Send", command=self._chat_send, width=10)
        self.chat_send_btn.pack(pady=2)
        ttk.Button(btn_col, text="Save instruction", command=self._chat_save_instruction_manual, width=12).pack(
            pady=2
        )
        ttk.Label(btn_col, text="Ctrl+Enter", font=("Segoe UI", 8)).pack()

        self._reload_chat_saved_prompts_ui()
        self._chat_append(
            "system",
            "Chat ready. This tab does not apply to jobs - use Jobs > Apply queued jobs now (LIVE). "
            "Standing rules: /remember your text, or use Save instruction.\n",
        )

    def _chat_append(self, role: str, text: str):
        if not hasattr(self, "chat_display"):
            return
        label = {"user": "You", "assistant": "Assistant", "system": "â€”"}.get(role, role)
        tag = role if role in ("user", "assistant", "system", "error") else "system"
        self.chat_display.configure(state=tk.NORMAL)
        if role != "system":
            self.chat_display.insert(tk.END, f"{label}:\n", tag)
        self.chat_display.insert(tk.END, text.rstrip() + "\n\n", tag)
        self.chat_display.see(tk.END)
        self.chat_display.configure(state=tk.DISABLED)

    def _chat_clear(self):
        self._chat_messages = []
        self.chat_display.configure(state=tk.NORMAL)
        self.chat_display.delete("1.0", tk.END)
        self.chat_display.configure(state=tk.DISABLED)
        self._chat_append("system", "Chat cleared.\n")

    def _reload_chat_saved_prompts_ui(self):
        if not hasattr(self, "chat_saved_prompts_box"):
            return
        from agents.chat_saved_prompts import load_prompts
        items = load_prompts()
        self.chat_saved_prompts_box.delete("1.0", tk.END)
        if items:
            self.chat_saved_prompts_box.insert(tk.END, "\n".join(f"- {p}" for p in items))
        else:
            self.chat_saved_prompts_box.insert(
                tk.END, "(none â€” use /remember â€¦ or Save instruction)"
            )

    def _save_chat_saved_prompts_from_ui(self):
        from agents.chat_saved_prompts import _write_prompts_list
        raw = self.chat_saved_prompts_box.get("1.0", tk.END).strip()
        items = []
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("- "):
                line = line[2:].strip()
            if line.startswith("* "):
                line = line[2:].strip()
            if line and not line.startswith("("):
                # strip _(saved â€¦)_ suffix if user edited
                line = re.sub(r"\s*_\(saved[^)]*\)_\s*$", "", line)
                items.append(line)
        _write_prompts_list(items)
        self._reload_chat_saved_prompts_ui()
        self.log_msg(f"Saved {len(items)} chat instruction(s)")

    def _clear_chat_saved_prompts(self):
        if not messagebox.askyesno("Clear instructions", "Remove all Chat saved prompts?"):
            return
        from agents.chat_saved_prompts import clear_prompts
        clear_prompts()
        self._reload_chat_saved_prompts_ui()
        self.log_msg("Cleared chat saved prompts")

    def _chat_save_instruction_manual(self):
        text = self.chat_input.get("1.0", tk.END).strip()
        if not text:
            messagebox.showinfo("Chat", "Type an instruction in the box first.")
            return
        from agents.chat_saved_prompts import append_prompt
        if append_prompt(text):
            self._reload_chat_saved_prompts_ui()
            self._chat_append("system", f"Saved instruction: {text[:120]}\n")
            self.log_msg("Chat instruction saved")
        else:
            messagebox.showinfo("Chat", "Instruction not saved (empty or duplicate).")

    def _chat_maybe_auto_save_instruction(self, text: str) -> bool:
        from agents.chat_saved_prompts import append_prompt, should_auto_save_instruction
        if not self.chat_auto_save_var.get():
            return False
        if not should_auto_save_instruction(text):
            return False
        if append_prompt(text):
            self._reload_chat_saved_prompts_ui()
            self._chat_append(
                "system",
                "Saved to Chat saved prompts (used by Apply too).\n",
            )
            return True
        return False

    def _chat_send(self):
        if self._chat_busy:
            return
        text = self.chat_input.get("1.0", tk.END).strip()
        if not text:
            return
        self.chat_input.delete("1.0", tk.END)
        self._chat_append("user", text)
        self._chat_maybe_auto_save_instruction(text)
        self._chat_busy = True
        self.chat_send_btn.configure(state=tk.DISABLED)
        self._chat_append("system", "Thinkingâ€¦\n")

        def task():
            from config.config import OLLAMA_BASE_URL, get_ollama_model
            from gui.ollama_chat import build_system_prompt, chat_completion

            model = get_ollama_model()

            selected = None
            if self.chat_include_job.get() and self._selected_id:
                selected = self.store.get_job(self._selected_id)

            system = build_system_prompt(
                include_profile=self.chat_include_profile.get(),
                include_requirements=self.chat_include_requirements.get(),
                selected_job=selected,
                last_user_text=text,
            )
            # Rebuild messages each send so context toggles apply
            messages = [{"role": "system", "content": system}]
            messages.extend(self._chat_messages)
            messages.append({"role": "user", "content": text})

            try:
                reply = chat_completion(messages, model, OLLAMA_BASE_URL)
                self._chat_messages.append({"role": "user", "content": text})
                self._chat_messages.append({"role": "assistant", "content": reply})
                self.after(0, lambda: self._chat_finish(reply, None))
            except Exception as e:
                self.after(0, lambda: self._chat_finish("", str(e)))

        threading.Thread(target=task, daemon=True).start()

    def _chat_finish(self, reply: str, error: str | None):
        self.chat_display.configure(state=tk.NORMAL)
        pos = self.chat_display.search("Thinkingâ€¦", "end", backwards=True)
        if pos:
            line = int(pos.split(".")[0])
            self.chat_display.delete(f"{line}.0", f"{line + 1}.0")
        self.chat_display.configure(state=tk.DISABLED)

        if error:
            self._chat_append("error", f"Error: {error}")
        else:
            self._chat_append("assistant", reply)
        self._chat_busy = False
        self.chat_send_btn.configure(state=tk.NORMAL)

    # â”€â”€ Console tab (live pipeline output) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _build_console_tab(self):
        top = ttk.Frame(self.console_tab, padding=6)
        top.pack(fill=tk.X)
        ttk.Label(
            top,
            text="Live output from Discover / Score / Apply (same style as PowerShell logs).",
        ).pack(side=tk.LEFT)
        self.console_autoscroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Auto-scroll", variable=self.console_autoscroll).pack(
            side=tk.RIGHT, padx=4
        )
        ttk.Button(top, text="Clear", command=self.clear_console).pack(side=tk.RIGHT, padx=4)
        ttk.Button(top, text="Show Jobs", command=lambda: self.notebook.select(self.jobs_tab)).pack(
            side=tk.RIGHT, padx=4
        )
        self._stop_btn_console = tk.Button(
            top, text="Stop current task", command=self._request_stop,
            bg="#c0392b", fg="white", activebackground="#922b21", activeforeground="white",
            font=("Segoe UI", 9, "bold"), relief=tk.FLAT, padx=8, pady=2, state=tk.DISABLED,
        )
        self._stop_btn_console.pack(side=tk.RIGHT, padx=(4, 8))

        self.console = scrolledtext.ScrolledText(
            self.console_tab,
            wrap=tk.NONE,
            font=("Consolas", 10),
            state=tk.DISABLED,
            bg="#0c0c0c",
            fg="#cccccc",
            insertbackground="#cccccc",
        )
        self.console.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
        self._write_console(
            "JobHuntrr console ready. Run an action from the Jobs tab to stream steps here.\n",
            autoscroll=True,
        )

    def clear_console(self):
        if not hasattr(self, "console"):
            return
        self.console.configure(state=tk.NORMAL)
        self.console.delete("1.0", tk.END)
        self.console.configure(state=tk.DISABLED)

    def _write_console(self, text: str, autoscroll: bool | None = None):
        if not hasattr(self, "console"):
            return
        follow = self.console_autoscroll.get() if autoscroll is None else autoscroll
        self.console.configure(state=tk.NORMAL)
        self.console.insert(tk.END, text)
        if follow:
            self.console.see(tk.END)
        self.console.configure(state=tk.DISABLED)

    def _console_log_line(self, msg: str):
        """Thread-safe: logging handler and workers call this."""
        def do():
            self._write_console(msg + "\n")
            if hasattr(self, "log"):
                self.log.configure(state=tk.NORMAL)
                self.log.insert(tk.END, msg + "\n")
                self.log.see(tk.END)
                self.log.configure(state=tk.DISABLED)

        if threading.current_thread() is threading.main_thread():
            do()
        else:
            self.after(0, do)

    def _show_console_tab(self):
        self.notebook.select(self.console_tab)

    # â”€â”€ LinkedIn DM outreach tab â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _build_linkedin_dm_tab(self):
        from agents.linkedin_outreach import default_companies_text, OUTREACH_STATUSES

        # Sub-notebook: Outreach | Signals
        self._linkedin_subnb = ttk.Notebook(self.linkedin_dm_tab)
        self._linkedin_subnb.pack(fill=tk.BOTH, expand=True)

        self._outreach_subtab = ttk.Frame(self._linkedin_subnb)
        self._signals_subtab = ttk.Frame(self._linkedin_subnb)
        self._linkedin_subnb.add(self._outreach_subtab, text="Outreach")
        self._linkedin_subnb.add(self._signals_subtab, text="ðŸ” Hidden Signals")

        self._build_signals_tab(self._signals_subtab)

        outer = ttk.Frame(self._outreach_subtab, padding=6)
        outer.pack(fill=tk.BOTH, expand=True)

        info = (
            "LinkedIn outreach: find leads outside the program (copy the LLM prompt into ChatGPT/Claude), "
            "import the returned CSV or profile URLs, then send from here using your saved LinkedIn session. "
            "No in-app people search â€” avoids captcha/checkpoints."
        )
        ttk.Label(outer, text=info, wraplength=1100).pack(anchor=tk.W)

        controls = ttk.LabelFrame(outer, text="Outreach run", padding=6)
        controls.pack(fill=tk.X, pady=6)

        left = ttk.Frame(controls)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        comp_hdr = ttk.Frame(left)
        comp_hdr.pack(fill=tk.X)
        ttk.Label(comp_hdr, text="Companies / campaign targets:").pack(side=tk.LEFT)
        self._dm_comp_status = ttk.Label(comp_hdr, text="loading...", foreground="#7f8c8d")
        self._dm_comp_status.pack(side=tk.LEFT, padx=8)
        ttk.Button(comp_hdr, text="â†º Refresh", command=self._refresh_outreach_companies).pack(side=tk.RIGHT)
        self.dm_companies_box = scrolledtext.ScrolledText(
            left, height=5, wrap=tk.WORD, font=("Consolas", 9)
        )
        self.dm_companies_box.pack(fill=tk.X, expand=True)
        self.after(100, self._refresh_outreach_companies)

        right = ttk.Frame(controls)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))
        ttk.Label(right, text="Quick focus / angle:").pack(anchor=tk.W)
        self.dm_focus_var = tk.StringVar(value="prioritize warm, high-signal UAE/GCC contacts")
        ttk.Entry(right, textvariable=self.dm_focus_var, width=46).pack(fill=tk.X)
        self.dm_public_search_var = tk.BooleanVar(value=False)
        ttk.Label(right, text="Max people/company:").pack(anchor=tk.W, pady=(6, 0))
        self.dm_max_people_var = tk.StringVar(value="8")
        ttk.Entry(right, textvariable=self.dm_max_people_var, width=8).pack(anchor=tk.W)

        btns = ttk.Frame(right)
        btns.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(btns, text="Copy LLM prompt", command=self.copy_linkedin_llm_prompt).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(btns, text="Generate outreach plan", command=self.generate_linkedin_outreach).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(btns, text="Reload", command=self.refresh_linkedin_outreach).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(btns, text="Import CSV", command=self.import_linkedin_outreach_csv_dialog).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(btns, text="Paste profile URLs", command=self.import_linkedin_profile_urls_dialog).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(btns, text="Export CSV", command=self.export_linkedin_outreach_csv).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(btns, text="Export Markdown", command=self.export_linkedin_outreach_md).pack(
            side=tk.LEFT, padx=2
        )

        paned = ttk.PanedWindow(outer, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        table_frame = ttk.Frame(paned)
        paned.add(table_frame, weight=3)
        cols = ("company", "cat", "person", "title", "pcat", "score", "status")
        self.dm_tree = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="extended")
        spec = {
            "company": ("Company", 140),
            "cat": ("Company category", 170),
            "person": ("Person", 160),
            "title": ("Title", 260),
            "pcat": ("Person category", 150),
            "score": ("Score", 54),
            "status": ("Status", 145),
        }
        for col, (label, width) in spec.items():
            self.dm_tree.heading(col, text=label)
            anchor = tk.W if col not in ("score",) else tk.CENTER
            self.dm_tree.column(col, width=width, anchor=anchor)
        dm_vsb = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.dm_tree.yview)
        self.dm_tree.configure(yscrollcommand=dm_vsb.set)
        self.dm_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        dm_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.dm_tree.bind("<<TreeviewSelect>>", self.on_linkedin_outreach_select)
        self.dm_tree.bind("<Double-1>", lambda e: self.open_linkedin_outreach_url())

        detail_frame = ttk.LabelFrame(paned, text="Outreach detail / tracking", padding=6)
        paned.add(detail_frame, weight=2)
        track = ttk.Frame(detail_frame)
        track.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(track, text="Status:").pack(side=tk.LEFT)
        self.dm_status_var = tk.StringVar(value="Not sent")
        ttk.Combobox(
            track,
            textvariable=self.dm_status_var,
            values=OUTREACH_STATUSES,
            state="readonly",
            width=24,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Label(track, text="Notes:").pack(side=tk.LEFT, padx=(8, 2))
        self.dm_notes_var = tk.StringVar()
        ttk.Entry(track, textvariable=self.dm_notes_var, width=55).pack(side=tk.LEFT, padx=4)
        ttk.Button(track, text="Save status/notes", command=self.save_linkedin_outreach_status).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(track, text="Delete selected", command=self.delete_linkedin_outreach_rows).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(track, text="Open LinkedIn URL", command=self.open_linkedin_outreach_url).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(track, text="Copy connection message", command=self.copy_linkedin_connection_message).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(track, text="Open + copy message", command=self.open_and_copy_linkedin_message).pack(
            side=tk.LEFT, padx=4
        )
        # â”€â”€ Send mode radio â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        ttk.Separator(track, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        self.dm_send_mode_var = tk.StringVar(value="connect")
        ttk.Label(track, text="Mode:").pack(side=tk.LEFT)
        ttk.Radiobutton(
            track, text="Connect", variable=self.dm_send_mode_var, value="connect"
        ).pack(side=tk.LEFT, padx=(2, 0))
        ttk.Radiobutton(
            track, text="Message", variable=self.dm_send_mode_var, value="message"
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Separator(track, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)

        self._dm_send_all_btn = ttk.Button(
            track, text="Send all pending", command=self.send_all_linkedin_pending
        )
        self._dm_send_all_btn.pack(side=tk.LEFT, padx=4)
        self._dm_send_btn = ttk.Button(track, text="Send selected", command=self.send_linkedin_dm)
        self._dm_send_btn.pack(side=tk.LEFT, padx=4)
        ttk.Button(track, text="Test (no send)", command=self.test_linkedin_dm).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(track, text="Copy follow-up", command=self.copy_linkedin_followup_message).pack(
            side=tk.LEFT, padx=4
        )

        self.dm_detail = scrolledtext.ScrolledText(
            detail_frame, height=12, wrap=tk.WORD, font=("Consolas", 9)
        )
        self.dm_detail.pack(fill=tk.BOTH, expand=True)
        self._dm_rows = []
        self.refresh_linkedin_outreach()

    def _dm_limit(self) -> int:
        try:
            return max(1, min(15, int(self.dm_max_people_var.get().strip() or "8")))
        except ValueError:
            return 8

    def _refresh_outreach_companies(self):
        """Auto-populate the Outreach company box from jobs DB + registry + outreach history."""
        def task():
            from agents.linkedin_outreach import generate_relevant_companies
            focus = self.dm_focus_var.get().strip() if hasattr(self, "dm_focus_var") else ""
            companies = generate_relevant_companies(limit=40, run_focus=focus)
            text = "\n".join(companies)
            def update():
                if hasattr(self, "dm_companies_box"):
                    self.dm_companies_box.delete("1.0", tk.END)
                    self.dm_companies_box.insert(tk.END, text)
                if hasattr(self, "_dm_comp_status"):
                    self._dm_comp_status.config(text=f"{len(companies)} companies")
            self.after(0, update)
        threading.Thread(target=task, daemon=True).start()

    def _refresh_signal_companies(self):
        """Auto-populate the Signals company box from the same source."""
        def task():
            from agents.linkedin_outreach import generate_relevant_companies
            companies = generate_relevant_companies(limit=40)
            text = "\n".join(companies)
            def update():
                if hasattr(self, "sig_companies_box"):
                    self.sig_companies_box.delete("1.0", tk.END)
                    self.sig_companies_box.insert(tk.END, text)
                if hasattr(self, "_sig_comp_status"):
                    self._sig_comp_status.config(text=f"{len(companies)} companies")
            self.after(0, update)
        threading.Thread(target=task, daemon=True).start()

    def generate_linkedin_outreach(self):
        companies = self.dm_companies_box.get("1.0", tk.END).strip()
        focus = self.dm_focus_var.get().strip()
        max_people = self._dm_limit()
        use_public = self.dm_public_search_var.get()

        def task():
            from agents.linkedin_outreach import build_outreach_plan
            rows = build_outreach_plan(
                companies,
                run_focus=focus,
                max_people_per_company=max_people,
                use_public_search=use_public,
            )
            self.after(0, lambda: self.refresh_linkedin_outreach(rows))

        self._run_bg(
            "Generate LinkedIn outreach plan",
            task,
            command_detail=(
                f"companies={len([x for x in companies.splitlines() if x.strip()])}, "
                f"max_people={max_people}, public_search={use_public}"
            ),
        )

    def generate_linkedin_companies(self):
        focus = self.dm_focus_var.get().strip()

        def task():
            from agents.linkedin_outreach import generate_relevant_companies
            companies = generate_relevant_companies(limit=30, run_focus=focus)
            text = "\n".join(companies)

            def update_box():
                self.dm_companies_box.delete("1.0", tk.END)
                self.dm_companies_box.insert(tk.END, text)
                self.log_msg(f"Generated {len(companies)} LinkedIn outreach company target(s)")

            self.after(0, update_box)

        self._run_bg(
            "Generate LinkedIn company targets",
            task,
            command_detail=f"focus={focus[:120] or '(none)'}",
        )

    def copy_linkedin_llm_prompt(self):
        companies = self.dm_companies_box.get("1.0", tk.END).strip()
        focus = self.dm_focus_var.get().strip()
        max_people = self._dm_limit()
        from agents.linkedin_outreach import generate_lead_discovery_prompt

        prompt = generate_lead_discovery_prompt(
            companies,
            run_focus=focus,
            max_people_per_company=max_people,
        )
        self.clipboard_clear()
        self.clipboard_append(prompt)
        self.update()
        self.log_msg("LinkedIn LLM lead-discovery prompt copied to clipboard")
        messagebox.showinfo(
            "LinkedIn DM",
            "Lead-discovery prompt copied to clipboard.\n\n"
            "Paste it into ChatGPT, Claude, or another LLM with web access. "
            "When you get the CSV back, use Import CSV here.",
        )

    def import_linkedin_outreach_csv_dialog(self):
        path = filedialog.askopenfilename(
            title="Import LinkedIn outreach CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialdir=str(Path(__file__).resolve().parent.parent / "data"),
        )
        if not path:
            return
        focus = self.dm_focus_var.get().strip()
        selected = self._selected_dm_row()
        default_company = (selected.get("Company") or "").strip() if selected else ""

        def task():
            from agents.linkedin_outreach import import_outreach_csv

            rows, status = import_outreach_csv(
                path,
                run_focus=focus,
                default_company=default_company,
            )
            self.log_msg(f"LinkedIn CSV import: {status}")

            def refresh_and_select():
                self.refresh_linkedin_outreach()
                if rows:
                    row_id = rows[0].get("id")
                    if row_id and row_id in self.dm_tree.get_children():
                        self.dm_tree.selection_set(row_id)
                        self.dm_tree.see(row_id)
                        self.on_linkedin_outreach_select()
                else:
                    messagebox.showinfo("LinkedIn DM", status)

            self.after(0, refresh_and_select)

        self._run_bg(
            "Import LinkedIn outreach CSV",
            task,
            command_detail=f"path={path}",
        )

    def send_all_linkedin_pending(self):
        from agents.linkedin_outreach import list_sendable_rows

        pending = list_sendable_rows()
        if not pending:
            messagebox.showinfo(
                "LinkedIn DM",
                "No pending rows with real profile URLs and messages.\n\n"
                "Import a CSV from your LLM or paste profile URLs first.",
            )
            return
        if not messagebox.askyesno(
            "LinkedIn DM",
            f"Send LinkedIn direct messages to {len(pending)} pending contact(s)?\n\n"
            "Uses your saved LinkedIn session. ~90s delay between sends.\n"
            "Stops if LinkedIn shows verification. It will not click Connect.",
        ):
            return

        max_count = len(pending)
        self._set_dm_send_all_cooldown()

        send_mode = getattr(self, "dm_send_mode_var", None)
        send_mode = send_mode.get() if send_mode else "connect"

        def task():
            from agents.linkedin_outreach import send_batch_outreach

            def on_entry_done():
                self.after(0, self._refresh_and_advance_dm_row)

            sent, failed, messages = send_batch_outreach(
                max_count=max_count,
                delay_seconds=12.0,
                headless=False,
                send_mode=send_mode,
                on_entry_done=on_entry_done,
            )
            summary = f"Batch send done: {sent} sent, {failed} failed"
            self.log_msg(summary)
            for line in messages[:20]:
                self.log_msg(f"  {line}")

            def done():
                self.refresh_linkedin_outreach()
                detail = "\n".join(messages[:12])
                if len(messages) > 12:
                    detail += f"\n... and {len(messages) - 12} more (see console)"
                messagebox.showinfo("LinkedIn DM", f"{summary}\n\n{detail}")

            self.after(0, done)

        self._run_bg(
            "Send all pending LinkedIn DMs",
            task,
            command_detail=f"pending={max_count}",
        )

    def _refresh_and_advance_dm_row(self):
        """Refresh the outreach table then auto-select the next unsent row."""
        self.refresh_linkedin_outreach()
        if not hasattr(self, "dm_tree"):
            return
        children = self.dm_tree.get_children()
        # Find the first row still marked as Not sent / empty
        for iid in children:
            vals = self.dm_tree.item(iid, "values")
            status = vals[6] if len(vals) > 6 else ""
            if status in ("Not sent", ""):
                self.dm_tree.selection_set(iid)
                self.dm_tree.see(iid)
                self.dm_tree.event_generate("<<TreeviewSelect>>")
                break

    def refresh_linkedin_outreach(self, rows=None):
        if not hasattr(self, "dm_tree"):
            return
        if rows is None:
            from agents.linkedin_outreach import load_rows
            rows = load_rows()
        self._dm_rows = rows
        for item in self.dm_tree.get_children():
            self.dm_tree.delete(item)
        for row in rows:
            iid = row.get("id") or ""
            self.dm_tree.insert(
                "",
                tk.END,
                iid=iid,
                values=(
                    row.get("Company", ""),
                    row.get("Company category", ""),
                    row.get("Person name", ""),
                    row.get("Person title", "")[:80],
                    row.get("Person category", ""),
                    row.get("Person priority score", ""),
                    row.get("Outreach status", "Not sent"),
                ),
            )

    def import_linkedin_profile_urls_dialog(self):
        selected = self._selected_dm_row()
        initial_company = (selected.get("Company") or "").strip() if selected else ""
        if not initial_company:
            first_company = self.dm_companies_box.get("1.0", tk.END).strip().splitlines()
            initial_company = first_company[0].strip() if first_company else ""

        win = tk.Toplevel(self)
        win.title("Import LinkedIn profile URLs")
        win.geometry("720x420")
        win.transient(self)
        win.grab_set()

        top = ttk.Frame(win, padding=8)
        top.pack(fill=tk.X)
        ttk.Label(top, text="Company for these contacts:").pack(side=tk.LEFT)
        company_var = tk.StringVar(value=initial_company)
        ttk.Entry(top, textvariable=company_var, width=40).pack(side=tk.LEFT, padx=6)

        ttk.Label(
            win,
            text="Paste LinkedIn /in/ profile URLs (one per line). Rows are created without opening LinkedIn; "
            "paste names/messages from your LLM CSV or edit after import.",
            wraplength=680,
        ).pack(anchor=tk.W, padx=8, pady=(4, 2))
        txt = scrolledtext.ScrolledText(win, height=14, wrap=tk.WORD, font=("Consolas", 9))
        txt.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        buttons = ttk.Frame(win, padding=8)
        buttons.pack(fill=tk.X)

        def run_import():
            urls_text = txt.get("1.0", tk.END).strip()
            company = company_var.get().strip()
            focus = self.dm_focus_var.get().strip()
            win.destroy()

            def task():
                from agents.linkedin_outreach import import_linkedin_profile_urls
                rows, status = import_linkedin_profile_urls(
                    urls_text,
                    company=company,
                    run_focus=focus,
                    headless=False,
                    scrape_profiles=False,
                )
                self.log_msg(f"LinkedIn profile import: {status}")

                def refresh_and_select():
                    self.refresh_linkedin_outreach()
                    if rows:
                        row_id = rows[0].get("id")
                        if row_id and row_id in self.dm_tree.get_children():
                            self.dm_tree.selection_set(row_id)
                            self.dm_tree.see(row_id)
                            self.on_linkedin_outreach_select()
                    else:
                        messagebox.showinfo("LinkedIn DM", status)

                self.after(0, refresh_and_select)

            self._run_bg(
                "Import LinkedIn profile URLs",
                task,
                command_detail=f"company={company or '(infer)'}, urls={len(urls_text.splitlines())}",
            )

        ttk.Button(buttons, text="Import profiles", command=run_import).pack(side=tk.RIGHT, padx=4)
        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side=tk.RIGHT, padx=4)

    def _selected_dm_row(self) -> dict:
        if not hasattr(self, "dm_tree"):
            return {}
        sel = self.dm_tree.selection()
        if not sel:
            return {}
        row_id = sel[0]
        for row in getattr(self, "_dm_rows", []):
            if row.get("id") == row_id:
                return row
        return {}

    def on_linkedin_outreach_select(self, _event=None):
        row = self._selected_dm_row()
        if not row:
            return
        self.dm_status_var.set(row.get("Outreach status") or "Not sent")
        self.dm_notes_var.set(row.get("Notes") or "")
        lines = [
            f"{row.get('Person name')} @ {row.get('Company')}",
            f"Title: {row.get('Person title')}",
            f"LinkedIn: {row.get('LinkedIn URL')}",
            f"Company category: {row.get('Company category')}  |  Company score: {row.get('Company priority score')}/10",
            f"Person category: {row.get('Person category')}  |  Person score: {row.get('Person priority score')}/10",
            f"Careers page: {row.get('Careers page URL')}",
            f"Suggested roles: {row.get('Suggested role types')}",
            f"Current relevant roles: {row.get('Current relevant roles') or '(none found locally)'}",
            "",
            "Why this person:",
            row.get("Why this person") or "",
            "",
            "Message angle:",
            row.get("Message angle") or "",
            "",
            "Connection message:",
            row.get("LinkedIn connection message") or "",
            "",
            "Follow-up after acceptance:",
            row.get("Follow-up message after acceptance") or "",
            "",
            "Tracking:",
            f"Status: {row.get('Outreach status')}",
            f"Date messaged: {row.get('Date messaged') or '-'}",
            f"Date accepted: {row.get('Date accepted') or '-'}",
            f"Date followed up: {row.get('Date followed up') or '-'}",
            f"Reply status: {row.get('Reply status') or '-'}",
            f"Notes: {row.get('Notes') or '-'}",
        ]
        self.dm_detail.delete("1.0", tk.END)
        self.dm_detail.insert(tk.END, "\n".join(lines))
        self._update_dm_send_button_state(row)

    def save_linkedin_outreach_status(self):
        row = self._selected_dm_row()
        if not row:
            messagebox.showinfo("LinkedIn DM", "Select an outreach row first.")
            return
        from agents.linkedin_outreach import update_row
        status = self.dm_status_var.get().strip() or "Not sent"
        fields = {
            "Outreach status": status,
            "Notes": self.dm_notes_var.get().strip(),
        }
        today = datetime.now().strftime("%Y-%m-%d")
        if status in ("Sent connection request", "Message sent") and not row.get("Date messaged"):
            fields["Date messaged"] = today
        elif status == "Accepted" and not row.get("Date accepted"):
            fields["Date accepted"] = today
        elif status == "Follow-up sent" and not row.get("Date followed up"):
            fields["Date followed up"] = today
        elif status in ("Replied", "Referred", "Rejected", "No response"):
            fields["Reply status"] = status
        update_row(row["id"], **fields)
        self.refresh_linkedin_outreach()
        self.log_msg("LinkedIn outreach status saved")

    def delete_linkedin_outreach_rows(self):
        if not hasattr(self, "dm_tree"):
            return
        sel = self.dm_tree.selection()
        if not sel:
            messagebox.showinfo("LinkedIn DM", "Select one or more rows to delete.")
            return
        count = len(sel)
        if not messagebox.askyesno(
            "LinkedIn DM",
            f"Delete {count} outreach row(s)? This cannot be undone.",
        ):
            return
        from agents.linkedin_outreach import delete_rows

        deleted = delete_rows(list(sel))
        self.refresh_linkedin_outreach()
        self.dm_detail.delete("1.0", tk.END)
        self.log_msg(f"Deleted {deleted} LinkedIn outreach row(s)")

    def open_linkedin_outreach_url(self):
        row = self._selected_dm_row()
        url = row.get("LinkedIn URL") if row else ""
        if url:
            webbrowser.open(url)
        else:
            messagebox.showinfo("LinkedIn DM", "No LinkedIn URL selected.")

    def _copy_text_to_clipboard(self, text: str, label: str):
        if not text:
            messagebox.showinfo("LinkedIn DM", f"No {label} available for this row.")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()
        self.log_msg(f"Copied LinkedIn {label} to clipboard")

    def copy_linkedin_connection_message(self):
        row = self._selected_dm_row()
        self._copy_text_to_clipboard(
            row.get("LinkedIn connection message", "") if row else "",
            "connection message",
        )

    def copy_linkedin_followup_message(self):
        row = self._selected_dm_row()
        self._copy_text_to_clipboard(
            row.get("Follow-up message after acceptance", "") if row else "",
            "follow-up message",
        )

    def open_and_copy_linkedin_message(self):
        self.copy_linkedin_connection_message()
        self.open_linkedin_outreach_url()

    def find_linkedin_people_for_selected(self):
        row = self._selected_dm_row()
        if not row:
            messagebox.showinfo("LinkedIn DM", "Select a company/manual-search row first.")
            return
        company = (row.get("Company") or "").strip()
        if not company:
            messagebox.showinfo("LinkedIn DM", "Selected row has no company.")
            return
        focus = self.dm_focus_var.get().strip()
        max_people = self._dm_limit()

        def task():
            from agents.linkedin_outreach import find_people_for_company
            rows, status = find_people_for_company(
                company,
                run_focus=focus,
                max_people=max_people,
                headless=False,
            )
            self.log_msg(f"LinkedIn people search: {status}")
            self.after(0, self.refresh_linkedin_outreach)
            if not rows:
                self.after(0, lambda: messagebox.showinfo("LinkedIn DM", status))

        self._run_bg(
            "Find LinkedIn people",
            task,
            command_detail=f"company={company}, max_people={max_people}",
        )

    def run_linkedin_dm_full_flow(self):
        row = self._selected_dm_row()
        if not row:
            messagebox.showinfo("LinkedIn DM", "Select a company/manual-search row first.")
            return
        company = (row.get("Company") or "").strip()
        if not company:
            messagebox.showinfo("LinkedIn DM", "Selected row has no company.")
            return
        focus = self.dm_focus_var.get().strip()
        max_people = self._dm_limit()
        self._set_dm_full_flow_cooldown()

        def task():
            from agents.linkedin_outreach import guided_send_for_company

            ok, status, sent_row = guided_send_for_company(
                company,
                run_focus=focus,
                max_people=max_people,
                headless=False,
            )
            self.log_msg(f"LinkedIn full DM flow: {status}")

            def refresh_and_select():
                self.refresh_linkedin_outreach()
                row_id = sent_row.get("id") if sent_row else ""
                if row_id and row_id in self.dm_tree.get_children():
                    self.dm_tree.selection_set(row_id)
                    self.dm_tree.see(row_id)
                    self.on_linkedin_outreach_select()
                if not ok:
                    messagebox.showwarning("LinkedIn DM", status)

            self.after(0, refresh_and_select)

        self._run_bg(
            "LinkedIn full DM flow",
            task,
            command_detail=f"company={company}, max_people={max_people}",
        )

    def send_linkedin_dm(self):
        self._run_linkedin_dm_send(dry_run=False)

    def test_linkedin_dm(self):
        self._run_linkedin_dm_send(dry_run=True)

    def _run_linkedin_dm_send(self, *, dry_run: bool):
        row = self._selected_dm_row()
        if not row:
            messagebox.showinfo("LinkedIn DM", "Select an outreach row first.")
            return
        person = (row.get("Person name") or "").strip()
        if not person or person.lower().startswith("manual linkedin search"):
            messagebox.showinfo(
                "LinkedIn DM",
                "Select a real person row with a linkedin.com/in/ URL.\n"
                "Import from LLM CSV or paste profile URLs first.",
            )
            return
        url = (row.get("LinkedIn URL") or "").strip()
        if "linkedin.com/in/" not in url.lower():
            messagebox.showinfo(
                "LinkedIn DM",
                "This row needs a real linkedin.com/in/ profile URL.\n"
                "Import from LLM CSV or paste profile URLs.",
            )
            return
        company = (row.get("Company") or "").strip()
        current_status = (row.get("Outreach status") or "Not sent").strip()
        sending_followup = current_status in ("Accepted", "Follow-up sent", "Replied", "Referred")
        message = (
            row.get("Follow-up message after acceptance", "")
            if sending_followup
            else row.get("LinkedIn connection message", "")
        ).strip()
        if not message:
            messagebox.showinfo("LinkedIn DM", "No message available for this row.")
            return
        if not url and not person:
            messagebox.showinfo("LinkedIn DM", "No LinkedIn URL or person name available.")
            return

        row_id = row.get("id")
        send_mode = getattr(self, "dm_send_mode_var", None)
        send_mode = send_mode.get() if send_mode else "connect"
        self._set_dm_send_cooldown()

        def task():
            from agents.linkedin_outreach import send_linkedin_connection

            ok, status_msg = send_linkedin_connection(
                url,
                person,
                company,
                message,
                headless=False,
                dry_run=dry_run,
                send_mode=send_mode,
            )
            prefix = "LinkedIn DM test result" if dry_run else "LinkedIn DM send result"
            self.log_msg(f"{prefix}: {status_msg}")
            if ok and not dry_run:
                from agents.linkedin_outreach import delete_rows
                delete_rows([row_id])
                self.after(0, self._refresh_and_advance_dm_row)
            elif ok and dry_run:
                self.after(0, lambda: messagebox.showinfo("LinkedIn DM", status_msg))
            else:
                self.after(0, lambda: messagebox.showwarning("LinkedIn DM", status_msg))

        self._run_bg(
            "Test one LinkedIn DM" if dry_run else "Send one LinkedIn DM",
            task,
            command_detail=f"person={person}, company={company}",
        )

    def _set_dm_send_cooldown(self):
        btn = getattr(self, "_dm_send_btn", None)
        if not btn:
            return
        btn.config(state=tk.DISABLED, text="Message cooldown...")

        def enable():
            if getattr(self, "_bg_running", False):
                self.after(5000, enable)
                return
            self._update_dm_send_button_state(self._selected_dm_row())

        self.after(60000, enable)

    def _set_dm_full_flow_cooldown(self):
        btn = getattr(self, "_dm_full_flow_btn", None)
        if not btn:
            return
        btn.config(state=tk.DISABLED, text="Flow cooldown...")

        def enable():
            if getattr(self, "_bg_running", False):
                self.after(5000, enable)
                return
            btn.config(state=tk.NORMAL, text="Find + send best match")

        self.after(60000, enable)

    def _update_dm_send_button_state(self, row: dict):
        btn = getattr(self, "_dm_send_btn", None)
        if not btn:
            return
        person = (row.get("Person name") or "").strip().lower()
        url = (row.get("LinkedIn URL") or "").strip().lower()
        if person.startswith("manual linkedin search") or "linkedin.com/in/" not in url:
            btn.config(state=tk.DISABLED, text="Import URL first")
        else:
            btn.config(state=tk.NORMAL, text="Message selected")

    def _set_dm_send_all_cooldown(self):
        btn = getattr(self, "_dm_send_all_btn", None)
        if not btn:
            return
        btn.config(state=tk.DISABLED, text="Batch cooldown...")

        def enable():
            if getattr(self, "_bg_running", False):
                self.after(5000, enable)
                return
            btn.config(state=tk.NORMAL, text="Message all pending")

        self.after(120000, enable)

    def export_linkedin_outreach_csv(self):
        from agents.linkedin_outreach import export_csv
        path = export_csv()
        self.log_msg(f"LinkedIn outreach CSV exported: {path}")
        messagebox.showinfo("LinkedIn DM", f"Exported CSV:\n{path}")

    def export_linkedin_outreach_md(self):
        from agents.linkedin_outreach import export_markdown
        path = export_markdown()
        self.log_msg(f"LinkedIn outreach Markdown exported: {path}")
        messagebox.showinfo("LinkedIn DM", f"Exported Markdown:\n{path}")

    def _start_console_logging(self):
        from gui.console_handler import attach_gui_console

        self._stop_console_logging()
        self._console_log_handler = attach_gui_console(self._console_log_line)

    def _stop_console_logging(self):
        from gui.console_handler import detach_gui_console

        detach_gui_console(self._console_log_handler)
        self._console_log_handler = None

    def _console_command_banner(self, label: str, extra: str = "") -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cmd = f"JobHuntrr> {label}"
        if extra:
            cmd += f"  ({extra})"
        self._write_console(f"\n[{ts}] PS C:\\Users\\Lordy\\jobhuntrr> {cmd}\n")

    # â”€â”€ Logging â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def log_msg(self, msg: str):
        self._console_log_line(msg)

    def _limit(self) -> int:
        try:
            return max(0, int(self.limit_var.get().strip() or "0"))
        except ValueError:
            return 0

    # â”€â”€ Jobs logic â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _get_filter_decision(self):
        label = self.filter_var.get()
        for name, dec in DECISION_FILTERS:
            if name == label:
                return dec
        return None

    def refresh_table(self):
        dec = self._get_filter_decision()
        search = self.search_var.get().strip()
        gcc = self.gcc_var.get()

        if dec == "pending":
            self._jobs = self.store.fetch_pending_apply(gcc_only=gcc)
        else:
            applied = True if dec == "applied" else None
            self._jobs = self.store.list_jobs(
                decision=dec if dec not in (None, "applied", "pending", "all_with_pending") else None,
                applied=applied,
                gcc_only=gcc,
                search=search,
            )
            if dec is None:
                self._jobs = [j for j in self._jobs if j.get("decision") != "discovered"]
            elif dec == "off_target":
                self._jobs = [j for j in self._jobs if j.get("outside_target_industry")]
            elif dec == "suggested_alternate":
                self._jobs = [j for j in self._jobs if j.get("suggested_alternate")]
            elif dec == "low_salary":
                self._jobs = [j for j in self._jobs if j.get("salary_below_minimum")]

        for i in self.tree.get_children():
            self.tree.delete(i)

        for job in self._jobs:
            disp = job.get("decision_display") or DECISION_DISPLAY.get(
                job.get("decision"), ""
            )
            disc = (job.get("discovered_at") or "")[:16].replace("T", " ")
            iid = str(job["id"])
            sal = ""
            if job.get("min_monthly_aed"):
                sal = f"{job['min_monthly_aed'] // 1000}k"
            elif job.get("salary_below_minimum"):
                sal = "LOW"
            method = _apply_method_display(job)
            mode = (job.get("apply_mode") or "")[:10]
            self.tree.insert(
                "", tk.END, iid=iid,
                tags=(disp,) if disp in TAG_COLORS else (),
                values=(
                    job.get("score", 0),
                    job.get("sps") or "",
                    job.get("ips") or "",
                    mode,
                    disp,
                    "â˜…" if job.get("suggested_alternate") else "",
                    sal,
                    "Yes" if job.get("outside_target_industry") else "",
                    (job.get("company") or "")[:36],
                    (job.get("title") or "")[:48],
                    (job.get("location") or "")[:28],
                    (job.get("positioning_angle") or "")[:12],
                    (job.get("source") or "")[:10],
                    method,
                    "Yes" if job.get("applied") else "",
                    disc,
                ),
            )

        st = self.store.stats()
        self.stats_label.config(
            text=f"Total: {st['total']} | Pending: {st['pending_apply']} | "
                 f"Pending score: {st.get('pending_score', 0)} | "
                 f"Applied: {st['applied']} | Showing: {len(self._jobs)}"
        )
        self._table_revision = self.store.revision()
        # Refresh sort indicator on headings
        self._refresh_sort_indicators()

    _SORT_KEY_MAP = {
        "score": lambda j: j.get("score") or 0,
        "sps": lambda j: j.get("sps") or 0,
        "ips": lambda j: j.get("ips") or 0,
        "mode": lambda j: (j.get("apply_mode") or "").lower(),
        "decision": lambda j: j.get("decision") or "",
        "alt": lambda j: 1 if j.get("suggested_alternate") else 0,
        "salary": lambda j: j.get("min_monthly_aed") or 0,
        "off_target": lambda j: 1 if j.get("outside_target_industry") else 0,
        "company": lambda j: (j.get("company") or "").lower(),
        "title": lambda j: (j.get("title") or "").lower(),
        "location": lambda j: (j.get("location") or "").lower(),
        "angle": lambda j: (j.get("positioning_angle") or "").lower(),
        "source": lambda j: (j.get("source") or "").lower(),
        "method": lambda j: _apply_method_display(j).lower(),
        "applied": lambda j: 1 if j.get("applied") else 0,
        "discovered": lambda j: j.get("discovered_at") or "",
    }

    def _sort_by(self, col: str):
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = col not in ("score", "salary")
        key_fn = self._SORT_KEY_MAP.get(col, lambda j: "")
        self._jobs.sort(key=key_fn, reverse=not self._sort_asc)
        # Re-render the tree with new order
        for item in self.tree.get_children():
            self.tree.delete(item)
        for job in self._jobs:
            disp = job.get("decision_display") or DECISION_DISPLAY.get(job.get("decision"), "")
            disc = (job.get("discovered_at") or "")[:16].replace("T", " ")
            iid = str(job["id"])
            sal = ""
            if job.get("min_monthly_aed"):
                sal = f"{job['min_monthly_aed'] // 1000}k"
            elif job.get("salary_below_minimum"):
                sal = "LOW"
            method = _apply_method_display(job)
            self.tree.insert(
                "", tk.END, iid=iid,
                tags=(disp,) if disp in TAG_COLORS else (),
                values=(
                    job.get("score", 0), disp,
                    "â˜…" if job.get("suggested_alternate") else "",
                    sal,
                    "Yes" if job.get("outside_target_industry") else "",
                    (job.get("company") or "")[:36],
                    (job.get("title") or "")[:48],
                    (job.get("location") or "")[:28],
                    (job.get("positioning_angle") or "")[:12],
                    (job.get("source") or "")[:10],
                    method,
                    "Yes" if job.get("applied") else "",
                    disc,
                ),
            )
        self._refresh_sort_indicators()

    def _refresh_sort_indicators(self):
        spec = {
            "score": "Score", "decision": "Decision", "alt": "Alt?",
            "salary": "Salary", "off_target": "Off-tgt", "company": "Company",
            "title": "Role", "location": "Location", "angle": "Angle",
            "source": "Source", "method": "Method",
            "applied": "Applied", "discovered": "Discovered",
        }
        for col, label in spec.items():
            if col == self._sort_col:
                arrow = " â–²" if self._sort_asc else " â–¼"
                self.tree.heading(col, text=label + arrow)
            else:
                self.tree.heading(col, text=label)

    def _get_selected_ids(self) -> list[int]:
        """Return all currently selected job IDs from the tree."""
        return [int(iid) for iid in self.tree.selection()]

    def on_select(self, _event=None):
        sel = self.tree.selection()
        n = len(sel)
        # Update selection count label
        if hasattr(self, "_sel_count_label"):
            self._sel_count_label.config(
                text=f"{n} selected",
                foreground="#2471a3" if n > 1 else "#7f8c8d",
            )
        if not sel:
            return

        # Multi-selection: show a summary instead of full details
        if n > 1:
            self._selected_id = int(sel[0])  # keep primary for single-job actions
            jobs = [self.store.get_job(int(iid)) for iid in sel]
            jobs = [j for j in jobs if j]
            decisions = {}
            for j in jobs:
                d = j.get("decision") or "unknown"
                decisions[d] = decisions.get(d, 0) + 1
            dec_summary = ", ".join(f"{v}Ã— {k}" for k, v in sorted(decisions.items()))
            avg_score = sum(j.get("score") or 0 for j in jobs) / max(len(jobs), 1)
            lines = [
                f"â”€â”€ {n} jobs selected â”€â”€",
                f"Avg score: {avg_score:.0f}/100",
                f"Decisions: {dec_summary}",
                "",
                "Use 'Bulk action on selected' above to act on all of them.",
                "Or click a single row to see its details.",
                "",
                "Selected jobs:",
            ]
            for j in jobs:
                lines.append(
                    f"  â€¢ {(j.get('title') or '')[:40]}  @  {(j.get('company') or '')[:30]}"
                    f"  [{j.get('decision') or 'â€”'}]  score={j.get('score') or 0}"
                )
            self.detail.delete("1.0", tk.END)
            self.detail.insert(tk.END, "\n".join(lines))
            return

        # Single selection: show full details
        self._selected_id = int(sel[0])
        job = self.store.get_job(self._selected_id)
        if not job:
            return
        self.notes_var.set(job.get("notes") or job.get("apply_notes") or "")
        lines = [
            f"{job['title']} @ {job['company']}",
            f"Score: {job['score']}/100  |  Decision: {job.get('decision_display')}",
            f"SPS: {job.get('sps') or '—'}  |  IPS: {job.get('ips') or '—'}  |  Mode: {job.get('apply_mode') or '—'}",
        ]
        if job.get("engine_action"):
            lines.append(f"Engine: {job.get('engine_action')} — {job.get('engine_reason') or ''}")
        if job.get("outreach_channel"):
            lines.append(f"Outreach waterfall: L{job.get('outreach_level')} ({job.get('outreach_channel')})")
        lines.append(f"Off-target industry: {'Yes' if job.get('outside_target_industry') else 'No'}")
        if job.get("outside_target_reason"):
            lines.append(f"  Reason: {job['outside_target_reason']}")
        if job.get("suggested_alternate"):
            lines.append("Suggested alternate (strong fit outside stated targets): Yes")
            if job.get("alternate_suggestion_reason"):
                lines.append(f"  {job['alternate_suggestion_reason']}")
        if job.get("salary_snippet") or job.get("min_monthly_aed"):
            lines.append(
                f"Salary: {job.get('salary_snippet') or 'â€”'} "
                f"({job.get('min_monthly_aed') or '?'}â€“{job.get('max_monthly_aed') or '?'} AED/mo parsed)"
            )
        if job.get("salary_below_minimum"):
            lines.append("  âš  Below your minimum salary threshold")
        lines.extend([
            f"Location: {job.get('location')}",
            f"Angle: {job.get('positioning_angle')}  |  Source: {job.get('source')}",
            f"Apply method: {job.get('apply_method') or 'â€”'}",
            f"Date posted: {job.get('date_posted') or 'â€”'}",
            f"Applied: {job.get('applied')}  |  Discovered: {job.get('discovered_at')}",
            f"Submission status: {job.get('submission_status') or 'â€”'}",
            f"URL: {job.get('job_url')}",
            f"Direct URL: {job.get('job_url_direct') or 'â€”'}",
        ])
        if job.get("submission_confirmed_at"):
            lines.append(f"Confirmed at: {job['submission_confirmed_at']}")
        if job.get("confirmation_url"):
            lines.append(f"Confirmation URL: {job['confirmation_url']}")
        if job.get("confirmation_text"):
            lines.append(f"Confirmation evidence: {job['confirmation_text']}")
        lines.extend([
            "",
            "Fit reason:",
            job.get("fit_reason") or "(none)",
            "",
            "Skip reason:",
            job.get("skip_reason") or "(none)",
            "",
            "Apply notes:",
            job.get("apply_notes") or "(none)",
        ])
        import json as _json
        jp = job.get("job_profile_json") or ""
        if jp:
            try:
                prof = _json.loads(jp)
                lines.append("")
                lines.append("â€” Structured job profile â€”")
                for key in ("role_summary", "department", "company_about", "experience_level"):
                    if prof.get(key):
                        lines.append(f"{key}: {str(prof[key])[:500]}")
                if prof.get("requirements"):
                    lines.append("requirements: " + "; ".join(
                        str(x) for x in (prof["requirements"][:8] if isinstance(prof["requirements"], list) else [prof["requirements"]])
                    ))
            except Exception:
                pass
        desc = job.get("description") or ""
        if desc:
            lines.extend(["", "â€” Full posting â€”", desc])
        self.detail.delete("1.0", tk.END)
        self.detail.insert(tk.END, "\n".join(lines))

    def save_notes(self):
        if not self._selected_id:
            messagebox.showinfo("JobHuntrr", "Select a job first")
            return
        notes = self.notes_var.get().strip()
        self.store.update_job(self._selected_id, notes=notes, apply_notes=notes)
        self.log_msg(f"Saved notes for job #{self._selected_id}")
        self.refresh_table()

    def open_url(self):
        if not self._selected_id:
            messagebox.showinfo("JobHuntrr", "Select a job first")
            return
        job = self.store.get_job(self._selected_id)
        url = (job.get("job_url") or job.get("job_url_direct")) if job else ""
        if url:
            webbrowser.open(url)
        else:
            messagebox.showwarning("JobHuntrr", "No URL for this job")

    def _run_bulk_action(self):
        """Execute the chosen bulk action on all selected rows."""
        ids = self._get_selected_ids()
        if not ids:
            messagebox.showinfo("JobHuntrr", "Select at least one job first.\n"
                                "(Click a row; Shift+click or Ctrl+click for multiple.)")
            return
        action = self._bulk_action_var.get()
        if action == "â€” choose â€”":
            messagebox.showinfo("JobHuntrr", "Choose an action from the dropdown first.")
            return
        n = len(ids)

        if action == "Delete selected":
            if not messagebox.askyesno(
                "Delete jobs",
                f"Permanently delete {n} selected job{'s' if n != 1 else ''}?"
            ):
                return
            for jid in ids:
                self.store.delete_job(jid)
            self._selected_id = None
            self.detail.delete("1.0", tk.END)
            self.refresh_table()
            self.log_msg(f"Deleted {n} job(s)")

        elif action == "Mark applied":
            for jid in ids:
                self.store.mark_applied(jid, "Marked applied via bulk action", applied=True)
            self.refresh_table()
            self.log_msg(f"Marked {n} job(s) as applied")

        elif action.startswith("Set: "):
            decision_map = {
                "Set: Auto Apply": "auto_apply",
                "Set: Manual Review": "manual_review",
                "Set: Skip": "skip",
            }
            decision = decision_map.get(action)
            if not decision:
                return
            for jid in ids:
                fields = {"decision": decision}
                if decision == "auto_apply":
                    fields.update({
                        "apply_attempts": 0,
                        "last_apply_attempt_at": "",
                        "submission_status": "",
                        "confirmation_checks": 0,
                        "last_confirmation_check_at": "",
                    })
                self.store.update_job(jid, **fields)
            self.refresh_table()
            self.log_msg(f"Set {n} job(s) â†’ {decision}")

        self._bulk_action_var.set("â€” choose â€”")

    def mark_applied(self):
        ids = self._get_selected_ids()
        if not ids:
            return
        note = self.notes_var.get() or "Marked applied from GUI"
        for jid in ids:
            self.store.mark_applied(jid, note, applied=True)
        self.refresh_table()
        self.log_msg(f"Marked {len(ids)} job(s) as applied")

    def set_decision(self, decision: str):
        ids = self._get_selected_ids()
        if not ids:
            return
        for jid in ids:
            fields = {"decision": decision}
            if decision == "auto_apply":
                fields.update({
                    "apply_attempts": 0,
                    "last_apply_attempt_at": "",
                    "submission_status": "",
                    "confirmation_checks": 0,
                    "last_confirmation_check_at": "",
                })
            self.store.update_job(jid, **fields)
        self.refresh_table()
        if len(ids) == 1:
            self.on_select()

    def delete_job(self):
        ids = self._get_selected_ids()
        if not ids:
            messagebox.showinfo("JobHuntrr", "Select a job first")
            return
        n = len(ids)
        if n == 1:
            job = self.store.get_job(ids[0])
            name = f"{job['title']} @ {job['company']}" if job else f"#{ids[0]}"
            prompt = f"Permanently delete:\n{name}?"
        else:
            prompt = f"Permanently delete {n} selected jobs?"
        if not messagebox.askyesno("Delete job", prompt):
            return
        for jid in ids:
            self.store.delete_job(jid)
        self._selected_id = None
        self.detail.delete("1.0", tk.END)
        self.refresh_table()
        self.log_msg(f"Deleted {n} job(s)")

    def edit_score(self):
        if not self._selected_id:
            return
        job = self.store.get_job(self._selected_id)
        if not job:
            return
        new = simpledialog.askinteger(
            "Edit score", "Score (0â€“100):",
            minvalue=0, maxvalue=100, initialvalue=job.get("score", 0),
        )
        if new is None:
            return
        self.store.update_job(self._selected_id, score=new)
        self.refresh_table()
        self.on_select()

    def edit_fit_reason(self):
        if not self._selected_id:
            return
        job = self.store.get_job(self._selected_id)
        if not job:
            return
        win = tk.Toplevel(self)
        win.title("Edit fit reason")
        win.geometry("520x200")
        txt = scrolledtext.ScrolledText(win, height=8, wrap=tk.WORD)
        txt.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        txt.insert(tk.END, job.get("fit_reason") or "")

        def save():
            self.store.update_job(
                self._selected_id, fit_reason=txt.get("1.0", tk.END).strip()
            )
            win.destroy()
            self.refresh_table()
            self.on_select()

        ttk.Button(win, text="Save", command=save).pack(pady=6)

    def _request_stop(self):
        """Called by the Stop button â€” signals all pipeline loops to exit cleanly."""
        _stop_flag.request_stop()
        self.log_msg("--- Stop requested â€” finishing current step then halting ---")
        # Keep button disabled after press to avoid double-clicks
        self._set_running_state(True, stopping=True)

    def _set_running_state(self, running: bool, stopping: bool = False):
        """Enable/disable the Stop button and update its label."""
        def _update():
            self._bg_running = running
            for btn in (
                getattr(self, "_stop_btn_console", None),
                getattr(self, "_stop_btn_jobs", None),
            ):
                if btn is None:
                    continue
                if running and not stopping:
                    btn.config(state=tk.NORMAL, text="Stop current task")
                else:
                    btn.config(state=tk.DISABLED,
                               text="Stopping..." if stopping else "Stop current task")
        self.after(0, _update)

    def _run_bg(self, label: str, func, *, command_detail: str = ""):
        def worker():
            _stop_flag.clear()
            self._set_running_state(True)
            self.after(0, self._show_console_tab)
            self._start_console_logging()
            self._console_command_banner(label, command_detail)
            self.log_msg(f"--- {label} started ---")
            try:
                func()
                self.log_msg(f"--- {label} finished ---")
            except _stop_flag.StopRequested as e:
                self.log_msg(f"--- Stopped: {e} ---")
            except Exception as e:
                self.log_msg(f"ERROR: {e}")
                import traceback
                self.log_msg(traceback.format_exc())
            finally:
                self._stop_console_logging()
                self._set_running_state(False)
            self.after(0, self.refresh_table)

        threading.Thread(target=worker, daemon=True).start()

    def run_discovery(self, apply: bool = False, live: bool = False):
        web_signal_search = self.web_signal_search_var.get()
        run_focus = self.run_focus_var.get().strip() if hasattr(self, "run_focus_var") else ""

        def task():
            from orchestrator import run_pipeline
            os.environ["WEB_SIGNAL_SEARCH"] = "1" if web_signal_search else "0"
            run_pipeline(
                dry_run=not live,
                apply_enabled=apply,
                headless=self.headless_var.get(),
                discover_limit=self._limit(),
                auto_enrich=self.auto_enrich_var.get(),
                validate_fit=self.validate_fit_var.get(),
                gcc_only=self.gcc_var.get(),
                include_previously_seen=self.revisit_seen_var.get(),
                progress_callback=lambda: self.after(0, self.refresh_table),
                run_focus=run_focus,
            )

        label = "Search + score"
        if apply:
            label += " + apply (LIVE)" if live else " + fill forms (NO SUBMIT)"
        detail = (
            f"dry_run={not live}, apply={apply}, headless={self.headless_var.get()}, "
            f"limit={self._limit()}, auto_enrich={self.auto_enrich_var.get()}, "
            f"gcc_only={self.gcc_var.get()}, revisit_seen={self.revisit_seen_var.get()}, "
            f"secondary_search_hidden_posts={web_signal_search}, "
            f"run_focus={run_focus[:120] or '(none)'}"
        )
        self._run_bg(label, task, command_detail=detail)

    def run_autonomous_cycle(self):
        """Default GUI action: discover, score, drain queue, and submit live."""
        self.run_discovery(apply=True, live=True)

    def start_continuous_afk(self):
        """Launch the scheduled autonomous worker while keeping the GUI open."""
        if self._autonomous_process and self._autonomous_process.poll() is None:
            self.log_msg("--- Repeating search + apply worker is already running ---")
            return
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        args = [sys.executable, "orchestrator.py", "--autonomous"]
        if self.revisit_seen_var.get():
            args.append("--include-previously-seen")
        child_env = os.environ.copy()
        child_env["WEB_SIGNAL_SEARCH"] = (
            "1" if self.web_signal_search_var.get() else "0"
        )
        self._autonomous_process = subprocess.Popen(
            args,
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            env=child_env,
        )
        self.log_msg(
            f"--- Repeating search + apply worker started (PID {self._autonomous_process.pid}) ---"
        )
        self._schedule_autonomous_refresh()

    def _schedule_autonomous_refresh(self):
        """Refresh Jobs while the separate autonomous worker is searching."""
        if self._autonomous_refresh_after_id is None:
            self._autonomous_refresh_after_id = self.after(
                2000, self._poll_autonomous_refresh
            )

    def _poll_autonomous_refresh(self):
        self._autonomous_refresh_after_id = None
        proc = self._autonomous_process
        if not proc or proc.poll() is not None:
            self._autonomous_process = None
            return
        self.refresh_table()
        self._schedule_autonomous_refresh()

    def _schedule_table_refresh(self):
        """Watch SQLite for writes made by scoring workers or external CLI runs."""
        if self._table_refresh_after_id is None:
            self._table_refresh_after_id = self.after(2000, self._poll_table_refresh)

    def _poll_table_refresh(self):
        self._table_refresh_after_id = None
        try:
            revision = self.store.revision()
            if revision != self._table_revision:
                self.refresh_table()
        finally:
            self._schedule_table_refresh()

    def stop_continuous_afk(self):
        """Stop the worker launched from this GUI session."""
        proc = self._autonomous_process
        if not proc or proc.poll() is not None:
            if self._autonomous_refresh_after_id is not None:
                self.after_cancel(self._autonomous_refresh_after_id)
                self._autonomous_refresh_after_id = None
            self.log_msg("--- Repeating search + apply worker is not running ---")
            return
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        self._autonomous_process = None
        if self._autonomous_refresh_after_id is not None:
            self.after_cancel(self._autonomous_refresh_after_id)
            self._autonomous_refresh_after_id = None
        self.log_msg("--- Repeating search + apply worker stopped ---")

    def run_rescore(self, gcc: bool, auto_only: bool):
        def task():
            from rescore_jobs import rescore_all
            rescore_all(
                gcc_only=gcc,
                auto_only=auto_only,
                limit=self._limit(),
                progress_callback=lambda: self.after(0, self.refresh_table),
            )

        parts = ["Rescore"]
        if auto_only:
            parts.append("auto-only")
        if gcc:
            parts.append("GCC")
        self._run_bg(" ".join(parts), task, command_detail=f"gcc={gcc}, auto_only={auto_only}")

    def run_score_pending(self):
        def task():
            from rescore_jobs import rescore_all
            rescore_all(
                gcc_only=self.gcc_var.get(),
                limit=self._limit(),
                progress_callback=lambda: self.after(0, self.refresh_table),
                pending_score_only=True,
            )

        self._run_bg(
            "Score pending jobs",
            task,
            command_detail=f"gcc={self.gcc_var.get()}, limit={self._limit()}",
        )

    def run_verify_apply_methods(self):
        def task():
            import os
            from agents.linkedin_apply_probe import verify_linkedin_jobs_apply_methods

            verify_linkedin_jobs_apply_methods(
                gcc_only=self.gcc_var.get(),
                limit=self._limit(),
                headless=self.headless_var.get(),
                linkedin_email=os.getenv("LINKEDIN_EMAIL", "").strip(),
                linkedin_password=os.getenv("LINKEDIN_PASSWORD", "").strip(),
                progress_callback=lambda: self.after(0, self.refresh_table),
            )

        self._run_bg(
            "Re-check LinkedIn apply methods",
            task,
            command_detail=f"gcc_only={self.gcc_var.get()}, limit={self._limit()}",
        )

    def run_apply(self, dry: bool = True, easy_apply_only: bool = False):
        def task():
            from apply_jobs import run_apply_batch
            run_apply_batch(
                dry_run=dry,
                gcc_only=self.gcc_var.get(),
                limit=self._limit(),
                validate_fit=self.validate_fit_var.get(),
                headless=self.headless_var.get(),
                easy_apply_only=easy_apply_only,
                run_focus=self.run_focus_var.get().strip(),
            )

        label = (
            "Test LinkedIn Easy Apply only (NO SUBMIT)"
            if dry and easy_apply_only else
            "Apply LinkedIn Easy Apply only (LIVE)"
            if easy_apply_only else
            "Test queued form fill (NO SUBMIT)"
            if dry else
            "Apply queued jobs (LIVE)"
        )
        self._run_bg(
            label,
            task,
            command_detail=(
                f"gcc_only={self.gcc_var.get()}, limit={self._limit()}, "
                f"easy_apply_only={easy_apply_only}"
            ),
        )

    def export_visible_jobs_csv(self):
        self.refresh_table()
        default_name = f"jobhuntrr_visible_jobs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path = filedialog.asksaveasfilename(
            title="Export visible jobs CSV",
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            fieldnames = [
                "id", "company", "title", "location", "score",
                "decision", "decision_display", "apply_method",
                "method_display", "source", "positioning_angle",
                "date_posted", "discovered_at", "job_url",
                "job_url_direct", "applied", "apply_attempts",
                "last_apply_attempt_at", "submission_status",
                "submission_confirmed_at", "confirmation_url",
                "confirmation_text", "confirmation_checks",
                "fit_reason", "skip_reason", "apply_notes", "notes",
                "outside_target_industry", "outside_target_reason",
                "salary_snippet", "min_monthly_aed", "max_monthly_aed",
                "salary_below_minimum", "matches_stated_targets",
                "suggested_alternate", "alternate_suggestion_reason",
                "created_at", "updated_at",
            ]
            rows = list(self._jobs)
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for job in rows:
                    row = {key: job.get(key, "") for key in fieldnames}
                    row["method_display"] = _apply_method_display(job)
                    row["applied"] = "yes" if job.get("applied") else "no"
                    row["outside_target_industry"] = "yes" if job.get("outside_target_industry") else "no"
                    row["salary_below_minimum"] = "yes" if job.get("salary_below_minimum") else "no"
                    row["matches_stated_targets"] = "yes" if job.get("matches_stated_targets") else "no"
                    row["suggested_alternate"] = "yes" if job.get("suggested_alternate") else "no"
                    writer.writerow(row)
            self.log_msg(
                f"Exported visible jobs CSV: {path} ({len(rows)} row(s))"
            )
            messagebox.showinfo(
                "Jobs CSV",
                f"Exported:\n{path}\n\nVisible rows exported: {len(rows)}",
            )
        except Exception as e:
            messagebox.showerror("Jobs CSV", f"Export failed:\n{e}")

    def kill_browsers(self):
        script = os.path.join(ROOT, "kill_browsers.py")
        if os.path.isfile(script):
            subprocess.run([sys.executable, script], cwd=ROOT, timeout=30)
            self.log_msg("Close browser windows script ran")
        else:
            self.log_msg("kill_browsers.py not found")

    def check_gaps_for_job(self):
        if not self._selected_id:
            messagebox.showinfo("JobHuntrr", "Select a job first")
            return
        job = self.store.get_job(self._selected_id)
        if not job:
            return
        from agents.profile_manager import find_profile_gaps
        missing = find_profile_gaps(job.get("description") or "")
        if not missing:
            messagebox.showinfo(
                "Profile gaps", "No obvious skill gaps vs this job description."
            )
            return
        self._show_gap_dialog(missing)

    # â”€â”€ Profile logic â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def reload_profile_editor(self):
        from agents.profile_manager import load_profile_body, migrate_inline_enrichment_to_dual_layer
        migrate_inline_enrichment_to_dual_layer()
        self.profile_editor.delete("1.0", tk.END)
        self.profile_editor.insert(tk.END, load_profile_body())
        self.reload_enhanced_editors()

    def reload_requirements_editor(self):
        from config.md_loader import load_requirements_sections
        sec = load_requirements_sections()
        self.req_editor.delete("1.0", tk.END)
        self.req_editor.insert(tk.END, sec.get("_main", ""))
        self.req_scoring_prompt.delete("1.0", tk.END)
        self.req_scoring_prompt.insert(tk.END, sec.get("Custom scoring prompt", ""))
        self.req_search_queries.delete("1.0", tk.END)
        self.req_search_queries.insert(tk.END, sec.get("Search queries", ""))
        self.req_search_prompt.delete("1.0", tk.END)
        self.req_search_prompt.insert(tk.END, sec.get("Custom search prompt", ""))
        self.reload_enhanced_editors()

    def _update_resume_status_label(self):
        if not hasattr(self, "resume_status_var"):
            return
        p = self.resume_path_var.get().strip() if hasattr(self, "resume_path_var") else ""
        if p and Path(p).is_file():
            self.resume_status_var.set(f"OK â€” file exists ({Path(p).name})")
        elif p:
            self.resume_status_var.set(f"WARNING â€” file not found: {p}")
        else:
            self.resume_status_var.set("No resume path set â€” Apply will fail uploads.")

    def _browse_resume(self):
        path = filedialog.askopenfilename(
            title="Select resume PDF",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
            initialdir=ROOT,
        )
        if path:
            self.resume_path_var.set(path)
            self._update_resume_status_label()

    def _browse_cover_letter(self):
        path = filedialog.askopenfilename(
            title="Select cover letter file",
            filetypes=[("PDF files", "*.pdf"), ("Word files", "*.docx"), ("Text files", "*.txt"), ("All files", "*.*")],
            initialdir=ROOT,
        )
        if path:
            self.cover_letter_path_var.set(path)

    def _upload_resume_pdf(self):
        """Copy chosen PDF into data/resume/ and set as apply resume."""
        path = filedialog.askopenfilename(
            title="Upload resume PDF for applications",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
            initialdir=str(Path.home() / "Downloads"),
        )
        if not path:
            return
        dest_dir = Path(ROOT) / "data" / "resume"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "active_resume.pdf"
        try:
            shutil.copy2(path, dest)
        except OSError as e:
            messagebox.showerror("Upload failed", str(e))
            return
        self.resume_path_var.set(str(dest))
        self._update_resume_status_label()
        if messagebox.askyesno(
            "Resume uploaded",
            f"Copied to:\n{dest}\n\nSave to profile_settings.json now?",
        ):
            self.save_all_profile_settings(silent=True)
            messagebox.showinfo("JobHuntrr", "Resume path saved. Apply will use this file.")

    def reload_enhanced_editors(self):
        from config.md_loader import load_profile_enhanced, load_requirements_enhanced
        if hasattr(self, "profile_enhanced_editor"):
            self.profile_enhanced_editor.delete("1.0", tk.END)
            self.profile_enhanced_editor.insert(tk.END, load_profile_enhanced() or "(empty â€” run Enrich)")
        if hasattr(self, "req_enhanced_editor"):
            self.req_enhanced_editor.delete("1.0", tk.END)
            self.req_enhanced_editor.insert(
                tk.END, load_requirements_enhanced() or "(empty â€” run Enrich)"
            )

    def save_profile_enhanced_editor(self):
        from config.md_loader import save_profile_enhanced
        from config.config import reload_candidate_profile
        body = self.profile_enhanced_editor.get("1.0", tk.END).strip()
        save_profile_enhanced(body)
        reload_candidate_profile()
        self.log_msg("Enhanced profile saved")

    def save_requirements_enhanced_editor(self):
        from config.md_loader import save_requirements_enhanced
        from config.applicant_requirements import reload_applicant_requirements_text
        body = self.req_enhanced_editor.get("1.0", tk.END).strip()
        save_requirements_enhanced(body)
        reload_applicant_requirements_text()
        self.log_msg("Enhanced requirements saved")

    def save_profile_editor(self):
        from agents.profile_manager import save_profile_body, load_links
        from config.config import reload_candidate_profile
        body = self.profile_editor.get("1.0", tk.END).strip()
        save_profile_body(body, load_links())
        reload_candidate_profile()
        self.log_msg("Source profile saved")
        messagebox.showinfo("JobHuntrr", "Source profile saved.")

    def save_requirements_editor(self):
        from config.md_loader import save_requirements_sections
        from config.applicant_requirements import reload_applicant_requirements_text
        save_requirements_sections({
            "_main": self.req_editor.get("1.0", tk.END).strip(),
            "Custom scoring prompt": self.req_scoring_prompt.get("1.0", tk.END).strip(),
            "Search queries": self.req_search_queries.get("1.0", tk.END).strip(),
            "Custom search prompt": self.req_search_prompt.get("1.0", tk.END).strip(),
        })
        reload_applicant_requirements_text()
        self.log_msg("Source requirements saved (general + scoring + search)")
        messagebox.showinfo("JobHuntrr", "Requirements saved.")

    def _notion_creds(self) -> tuple[str, str]:
        return (
            self.env_vars.get("NOTION_TOKEN", tk.StringVar()).get().strip(),
            self.env_vars.get("NOTION_DATABASE_ID", tk.StringVar()).get().strip(),
        )

    def pull_from_notion(self):
        token, db_id = self._notion_creds()

        def task():
            from agents.notion_sync import pull_from_notion
            r = pull_from_notion(
                token=token,
                database_id=db_id,
                gcc_only=self.notion_sync_gcc_var.get(),
            )
            self.log_msg(
                f"Notion pull: {r['total_notion']} rows fetched â€” "
                f"{r['imported']} new, {r['updated']} updated, {r['skipped']} skipped"
            )
            if r['skipped'] > 0:
                self.log_msg(
                    f"  ({r['skipped']} skipped â€” check console for details; "
                    "common cause: rows with no title/company)"
                )
            self.after(0, self.refresh_table)

        self._run_bg("Pull from Notion", task)

    def push_to_notion(self):
        token, db_id = self._notion_creds()

        def task():
            from agents.notion_sync import push_to_notion
            r = push_to_notion(
                token=token,
                database_id=db_id,
                gcc_only=self.notion_sync_gcc_var.get(),
                limit=self._limit(),
            )
            self.log_msg(
                f"Notion push: {r['created']} created, {r['updated']} updated, "
                f"{r['failed']} failed (of {r['total_local']} local jobs)"
            )

        self._run_bg("Push to Notion", task)

    def restore_profile_backup(self):
        from agents.profile_manager import BACKUP_DIR, PROFILE_PATH
        if not BACKUP_DIR.exists():
            messagebox.showinfo("JobHuntrr", "No backups found.")
            return
        backups = sorted(BACKUP_DIR.glob("applicant_profile_*.md"), reverse=True)
        if not backups:
            messagebox.showinfo("JobHuntrr", "No backups found.")
            return
        latest = backups[0]
        if not messagebox.askyesno(
            "Restore backup",
            f"Replace current profile with backup?\n\n{latest.name}",
        ):
            return
        PROFILE_PATH.write_text(latest.read_text(encoding="utf-8"), encoding="utf-8")
        self.reload_profile_editor()
        self.log_msg(f"Restored profile from {latest.name}")
        messagebox.showinfo("JobHuntrr", "Profile restored from backup.")

    def enrich_profile(self):
        from agents.profile_manager import validate_linkedin_required, run_profile_enrich
        from config.md_loader import use_dual_layer

        links = self._collect_links_from_gui()
        ok, err = validate_linkedin_required(links)
        if not ok:
            messagebox.showerror("LinkedIn required", err)
            return
        if not self.save_all_profile_settings(silent=True):
            return
        dual_layer = use_dual_layer() and not self.safe_enrich_var.get()
        if dual_layer:
            mode = "dual layer â†’ data/enhanced/ (source profile unchanged)"
        else:
            mode = (
                "legacy: append to source profile"
                if self.safe_enrich_var.get()
                else "legacy: full merge into source"
            )
        if not messagebox.askyesno(
            "Enrich profile",
            f"Mode: {mode}. Backup saved to data/profile_backups/ first.\n\n"
            "Opens LinkedIn, GitHub, website, and parses your resume (1â€“3 min). Continue?",
        ):
            return

        resume = self.resume_path_var.get().strip()

        def task():
            from agents.profile_manager import save_links, backup_profile
            from config.applicant_requirements import reload_applicant_requirements_text
            from config.config import reload_candidate_profile

            save_links(links)
            backup_profile()
            result = run_profile_enrich(
                links,
                resume_path=resume,
                dual_layer=dual_layer,
                safe_mode=self.safe_enrich_var.get(),
                headless=False,
            )
            self.log_msg(result.get("message", "Enrich finished"))
            if not result.get("ok"):
                self.after(
                    0,
                    lambda: messagebox.showerror("Enrich failed", result.get("message", "Unknown error")),
                )
                return
            reload_candidate_profile()
            reload_applicant_requirements_text()
            self.after(0, self.reload_profile_editor)
            self.after(0, self.reload_enhanced_editors)
            summary = result.get("message", "Done")
            if not result.get("bullets_added"):
                summary += (
                    "\n\nTip: If nothing new appeared, facts may already be in your profile. "
                    "Re-run after updating resume or links, or run setup_linkedin.py if LinkedIn failed."
                )
            self.after(0, lambda: messagebox.showinfo("Enrich profile", summary))

        self._run_bg("Enrich profile", task)

    def parse_resume_only(self):
        from agents.profile_manager import extract_resume_text, load_profile_body, save_profile_body, load_links
        from config.md_loader import use_dual_layer, load_profile_enhanced, save_profile_enhanced
        from config.config import reload_candidate_profile
        path = self.resume_path_var.get().strip()
        text = extract_resume_text(path)
        if not text:
            messagebox.showerror("Resume", "Could not read PDF. Install: pip install pypdf")
            return
        block = f"\n\n## Resume extract (auto)\n\n{text[:4000]}\n"
        if use_dual_layer():
            body = (load_profile_enhanced() or "").strip() + block
            save_profile_enhanced(body)
            reload_candidate_profile()
            self.reload_enhanced_editors()
            self.log_msg("Resume text appended to enhanced profile")
        else:
            body = load_profile_body() + block
            save_profile_body(body, load_links())
            self.reload_profile_editor()
            self.log_msg("Resume text appended to source profile")

    def fill_gaps_manual(self):
        from agents.profile_manager import find_profile_gaps
        missing = find_profile_gaps()
        if not missing:
            skill = simpledialog.askstring(
                "Add skill", "No gaps detected. Enter a skill/tool to add:"
            )
            if skill:
                missing = [skill.lower()]
            else:
                return
        self._show_gap_dialog(missing[:12])

    def _show_gap_dialog(self, missing: list[str]):
        win = tk.Toplevel(self)
        win.title("Profile gaps â€” add missing info")
        win.geometry("520x400")
        ttk.Label(
            win,
            text="These skills appear required but are missing from your profile. "
                 "Add experience level for each (or leave blank to skip):",
            wraplength=480,
        ).pack(padx=10, pady=10)
        entries = {}
        frame = ttk.Frame(win)
        frame.pack(fill=tk.BOTH, expand=True, padx=10)
        for skill in missing:
            row = ttk.Frame(frame)
            row.pack(fill=tk.X, pady=3)
            ttk.Label(row, text=skill, width=22).pack(side=tk.LEFT)
            e = ttk.Entry(row, width=40)
            e.pack(side=tk.LEFT, fill=tk.X, expand=True)
            entries[skill] = e

        def save():
            from agents.profile_manager import apply_skill_answers
            answers = {k: entries[k].get().strip() for k in entries if entries[k].get().strip()}
            if answers:
                apply_skill_answers(answers)
                self.reload_profile_editor()
                self.log_msg(f"Added skills: {', '.join(answers.keys())}")
            win.destroy()

        ttk.Button(win, text="Save to profile", command=save).pack(pady=10)


def main():
    app = JobHunterApp()
    app.mainloop()


if __name__ == "__main__":
    main()
