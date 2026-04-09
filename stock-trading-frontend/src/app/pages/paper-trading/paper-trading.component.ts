import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';

@Component({
  selector: 'app-paper-trading',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './paper-trading.component.html',
  styleUrl: './paper-trading.component.scss'
})
export class PaperTradingComponent implements OnInit {
  trades: any[] = [];
  openTrades: any[] = [];
  closedTrades: any[] = [];
  analytics: any = null;
  loading = true;
  activeTab = 'open';

  constructor(private api: ApiService) {}

  ngOnInit() {
    this.loadTrades();
    this.loadAnalytics();
  }

  loadTrades() {
    this.loading = true;
    this.api.getPaperTrades().subscribe({
      next: (data) => {
        this.trades = data;
        this.openTrades = data.filter((t: any) => t.status === 'OPEN');
        this.closedTrades = data.filter((t: any) => t.status === 'CLOSED');
        this.loading = false;
      },
      error: () => { this.loading = false; }
    });
  }

  loadAnalytics() {
    this.api.getAnalytics().subscribe({
      next: (data) => { this.analytics = data; },
    });
  }

  closeTrade(trade: any) {
    const exitPrice = trade.entry_price * (1 + (Math.random() * 0.04 - 0.01));
    this.api.closePaperTrade(trade.id, parseFloat(exitPrice.toFixed(2)), 'Manual close').subscribe({
      next: () => {
        this.loadTrades();
        this.loadAnalytics();
      }
    });
  }

  formatSymbol(symbol: string): string {
    return symbol.replace(/-EQ$/, '').replace(/^NSE:/, '');
  }

  formatDuration(minutes: number): string {
    if (!minutes) return '-';
    if (minutes < 60) return `${minutes}m`;
    if (minutes < 1440) return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
    return `${Math.floor(minutes / 1440)}d ${Math.floor((minutes % 1440) / 60)}h`;
  }

  getResultClass(result: string): string {
    if (result === 'WIN') return 'badge-win';
    if (result === 'LOSS') return 'badge-loss';
    return 'badge-neutral';
  }

  getPnlClass(val: number): string {
    return val > 0 ? 'positive' : val < 0 ? 'negative' : '';
  }
}
