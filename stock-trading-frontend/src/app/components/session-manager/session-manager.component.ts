import { Component, EventEmitter, Input, OnDestroy, OnInit, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';
import { ToastService } from '../../services/toast.service';

export interface SessionSummary {
  id: number;
  session_name: string;
  starting_capital: number;
  status: string;
  created_at: string;
  closed_at?: string | null;
  closing_capital?: number | null;
  total_pnl?: number | null;
  total_trades?: number | null;
  win_count?: number | null;
  loss_count?: number | null;
  summary?: {
    total_trades: number;
    open_trades: number;
    closed_trades: number;
    win_count: number;
    loss_count: number;
    total_pnl: number;
    total_net_pnl: number;
    open_exposure: number;
  };
  running_pnl?: number;
}

@Component({
  selector: 'app-session-manager',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './session-manager.component.html',
  styleUrl: './session-manager.component.scss'
})
export class SessionManagerComponent implements OnInit, OnDestroy {
  @Input() variant: 'compact' | 'full' = 'compact';
  @Output() sessionChanged = new EventEmitter<SessionSummary | null>();

  activeSession: SessionSummary | null = null;
  sessions: SessionSummary[] = [];
  loading = false;
  showDialog = false;
  showHistory = false;

  newName = '';
  newCapital = 100000;
  newNotes = '';
  creating = false;

  private refreshTimer: any = null;

  constructor(private api: ApiService, private toast: ToastService) {}

  ngOnInit() {
    this.refresh();
    // Keep the P&L indicator in sync when trades close in the background.
    this.refreshTimer = setInterval(() => this.refreshActive(), 20000);
  }

  ngOnDestroy() {
    if (this.refreshTimer) clearInterval(this.refreshTimer);
  }

  refresh() {
    this.loading = true;
    this.api.getSessions().subscribe({
      next: (rows: SessionSummary[]) => {
        this.sessions = rows || [];
        const active = this.sessions.find(s => s.status === 'ACTIVE') || null;
        this.activeSession = active;
        this.sessionChanged.emit(active);
        this.loading = false;
      },
      error: () => {
        this.loading = false;
        this.toast.error('Sessions', 'Failed to load sessions');
      }
    });
  }

  refreshActive() {
    this.api.getActiveSession().subscribe({
      next: (res: any) => {
        if (res?.active) {
          this.activeSession = res as SessionSummary;
        } else {
          this.activeSession = null;
        }
      },
      error: () => { /* swallow — periodic refresh */ }
    });
  }

  openNewDialog() {
    this.newName = `Session ${new Date().toLocaleDateString()}`;
    this.newCapital = this.activeSession?.starting_capital || 100000;
    this.newNotes = '';
    this.showDialog = true;
  }

  cancelNew() { this.showDialog = false; }

  confirmNew() {
    const name = (this.newName || '').trim();
    if (!name) {
      this.toast.error('Session', 'Session name is required');
      return;
    }
    const capital = Number(this.newCapital);
    if (!Number.isFinite(capital) || capital <= 0) {
      this.toast.error('Session', 'Starting capital must be positive');
      return;
    }
    this.creating = true;
    this.api.createSession({
      session_name: name,
      starting_capital: capital,
      notes: this.newNotes || undefined,
    }).subscribe({
      next: () => {
        this.creating = false;
        this.showDialog = false;
        this.toast.success('Session', `Started "${name}" with ₹${capital.toLocaleString()}`);
        this.refresh();
      },
      error: (err) => {
        this.creating = false;
        const msg = err?.error?.detail || 'Failed to start session';
        this.toast.error('Session', msg);
      }
    });
  }

  closeCurrent() {
    if (!this.activeSession) return;
    if (!confirm(`Close session "${this.activeSession.session_name}"? All open trades will be squared off.`)) return;
    const id = this.activeSession.id;
    this.api.closeSession(id).subscribe({
      next: (res: any) => {
        const squared = res?.squared_off_open_trades ?? 0;
        this.toast.success('Session', `Closed (${squared} trade${squared === 1 ? '' : 's'} squared off)`);
        this.refresh();
      },
      error: (err) => {
        const msg = err?.error?.detail || 'Failed to close session';
        this.toast.error('Session', msg);
      }
    });
  }

  pnlClass(pnl: number | null | undefined): string {
    const v = Number(pnl || 0);
    if (v > 0) return 'pnl-pos';
    if (v < 0) return 'pnl-neg';
    return 'pnl-zero';
  }

  trackById(_: number, item: SessionSummary): number { return item.id; }

  get runningPnl(): number {
    const s = this.activeSession;
    if (!s) return 0;
    return Number(s.running_pnl ?? s.summary?.total_pnl ?? s.total_pnl ?? 0);
  }

  get runningCapital(): number {
    const s = this.activeSession;
    if (!s) return 0;
    return Number(s.starting_capital || 0) + this.runningPnl;
  }
}
