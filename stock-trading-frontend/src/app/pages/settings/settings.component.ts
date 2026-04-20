import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './settings.component.html',
  styleUrl: './settings.component.scss'
})
export class SettingsComponent implements OnInit {
  settings: any = {};
  fyersStatus: any = null;
  scannerStatus: any = null;
  saveMessage = '';

  // Quantity calculator
  calcEntryPrice: number = 0;
  calcResult: any = null;

  constructor(private api: ApiService) {}

  ngOnInit() {
    this.loadSettings();
    this.api.getFyersStatus().subscribe({
      next: (res) => { this.fyersStatus = res; },
      error: () => {}
    });
    this.api.getScannerStatus().subscribe({
      next: (res) => { this.scannerStatus = res; },
      error: () => {}
    });
  }

  loadSettings() {
    this.api.getSettings().subscribe({
      next: (res) => { this.settings = res.settings || res || {}; },
      error: () => {}
    });
  }

  saveSettings() {
    const update: any = {
      simulated_capital: this.settings.simulated_capital,
      default_sl_percent: this.settings.default_sl_percent,
      default_target_percent: this.settings.default_target_percent,
      default_quantity: this.settings.default_quantity,
      max_open_trades: this.settings.max_open_trades,
      max_trades_per_day: this.settings.max_trades_per_day,
      min_net_profit_per_trade: this.settings.min_net_profit_per_trade,
      min_profit_to_cost_ratio: this.settings.min_profit_to_cost_ratio,
      day_max_loss_paper: this.settings.day_max_loss_paper,
      day_profit_target_paper: this.settings.day_profit_target_paper,
      day_max_loss_live: this.settings.day_max_loss_live,
      day_profit_target_live: this.settings.day_profit_target_live,
      scan_frequency_minutes: this.settings.scan_frequency_minutes,
      top_movers_count: this.settings.top_movers_count,
      min_change_pct: this.settings.min_change_pct,
      min_volume: this.settings.min_volume,
      auto_trade_enabled: this.settings.auto_trade_enabled,
      auto_quantity_enabled: this.settings.auto_quantity_enabled,
      product_type: this.settings.product_type,
      // Auto-Trade Engine thresholds
      min_score_for_trade: this.settings.min_score_for_trade,
      min_confidence_for_trade: this.settings.min_confidence_for_trade,
      max_active_trades: this.settings.max_active_trades,
    };
    this.api.updateSettings(update).subscribe({
      next: () => {
        this.saveMessage = 'Settings saved!';
        setTimeout(() => this.saveMessage = '', 3000);
      },
      error: () => { this.saveMessage = 'Error saving settings'; }
    });
  }

  calculateQuantity() {
    if (!this.calcEntryPrice || this.calcEntryPrice <= 0) return;
    this.api.calculateQuantity(
      this.calcEntryPrice,
      this.settings.default_sl_percent,
      this.settings.default_target_percent,
      this.settings.trade_mode || 'PAPER'
    ).subscribe({
      next: (res) => { this.calcResult = res; },
      error: () => { this.calcResult = { success: false, error: 'Calculation failed' }; }
    });
  }

  connectFyers() {
    this.api.getFyersAuthUrl().subscribe({
      next: (res) => { if (res.auth_url) window.open(res.auth_url, '_blank'); }
    });
  }

  toggleScanner() {
    this.api.toggleScanner().subscribe({
      next: (res) => { if (this.scannerStatus) this.scannerStatus.scanner_running = res.scanner_running; }
    });
  }
}
