import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';

interface JournalTrade {
  id: number;
  symbol: string;
  side: string;
  product_type: string;
  entry_price: number;
  entry_time: string;
  exit_price: number | null;
  exit_time: string | null;
  quantity: number;
  stop_loss: number | null;
  target: number | null;
  status: string;
  result: string | null;
  pnl_percent: number | null;
  pnl_amount: number | null;
  gross_pnl: number | null;
  net_pnl: number | null;
  brokerage: number;
  stt: number;
  exchange_charges: number;
  gst: number;
  sebi_charges: number;
  stamp_duty: number;
  duration_minutes: number | null;
  exit_reason: string | null;
  expanded?: boolean;
  audit?: AuditEvent[];
  audit_loading?: boolean;
}

interface AuditEvent {
  id: number;
  event_type: string;
  timestamp: string;
  old_value: any;
  new_value: any;
  reason: string | null;
  trigger_data: any;
}

@Component({
  selector: 'app-trade-journal',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './trade-journal.component.html',
  styleUrl: './trade-journal.component.scss',
})
export class TradeJournalComponent implements OnInit {
  trades: JournalTrade[] = [];
  loading = false;
  symbolFilter = '';
  resultFilter: '' | 'WIN' | 'LOSS' | 'OPEN' = '';
  statusFilter: '' | 'OPEN' | 'CLOSED' = '';

  constructor(private api: ApiService) {}

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading = true;
    this.api.getPaperTrades(this.statusFilter || undefined).subscribe({
      next: (rows: any[]) => {
        this.trades = (rows || []).map((r) => ({ ...r, expanded: false }));
        this.loading = false;
      },
      error: () => {
        this.loading = false;
      },
    });
  }

  get filteredTrades(): JournalTrade[] {
    const sym = this.symbolFilter.trim().toLowerCase();
    return this.trades.filter((t) => {
      if (sym && !(t.symbol || '').toLowerCase().includes(sym)) return false;
      if (this.resultFilter && t.result !== this.resultFilter) return false;
      return true;
    });
  }

  toggle(t: JournalTrade): void {
    t.expanded = !t.expanded;
    if (t.expanded && !t.audit && !t.audit_loading) {
      t.audit_loading = true;
      this.api.getPaperTradeAudit(t.id).subscribe({
        next: (events: AuditEvent[]) => {
          t.audit = events || [];
          t.audit_loading = false;
        },
        error: () => {
          t.audit = [];
          t.audit_loading = false;
        },
      });
    }
  }

  totalCharges(t: JournalTrade): number {
    return (
      (t.brokerage || 0) +
      (t.stt || 0) +
      (t.exchange_charges || 0) +
      (t.gst || 0) +
      (t.sebi_charges || 0) +
      (t.stamp_duty || 0)
    );
  }

  pnlClass(v: number | null | undefined): string {
    if (v == null) return '';
    return v > 0 ? 'pos' : v < 0 ? 'neg' : '';
  }

  eventClass(evt: string): string {
    const cls: { [k: string]: string } = {
      TRADE_PLACED: 'evt-placed',
      TRADE_CLOSED: 'evt-closed',
      INTRADAY_SQUARE_OFF: 'evt-squareoff',
      TRAILING_PROFIT: 'evt-trail',
      TREND_REVERSAL: 'evt-reverse',
      REANALYSIS: 'evt-re',
      SL_CHANGED: 'evt-sl',
      TARGET_CHANGED: 'evt-tgt',
      PRODUCT_TYPE_DECISION: 'evt-route',
      TIMING_REJECTED: 'evt-reject',
    };
    return cls[evt] || 'evt-default';
  }

  fmtKV(obj: any): string {
    if (!obj || typeof obj !== 'object') return '';
    return Object.entries(obj)
      .map(([k, v]) => `${k}=${v}`)
      .join(', ');
  }
}
