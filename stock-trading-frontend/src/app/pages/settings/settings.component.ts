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
      next: (res) => {
        this.settings = res.settings || res || {};
        // Hydrate the Auto-Trade Engine thresholds from /gates so the form
        // shows the effective value (override \u2192 settings column \u2192 default)
        // instead of leaving the inputs empty.
        this.api.getGates().subscribe({
          next: (g: any) => {
            const byKey: { [k: string]: any } = {};
            (g?.gates || []).forEach((row: any) => { if (row?.key) byKey[row.key] = row; });
            const minScore = byKey['min_score']?.current;
            const minConf = byKey['min_confidence']?.current;
            if (minScore !== undefined && this.settings.min_score_for_trade == null) {
              this.settings.min_score_for_trade = minScore;
            }
            if (minConf !== undefined && this.settings.min_confidence_for_trade == null) {
              this.settings.min_confidence_for_trade = minConf;
            }
          },
          error: () => {},
        });
      },
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
    };
    // The Auto-Trade Engine score / confidence thresholds live in the
    // ``gate_overrides`` JSON rather than as columns on ``trading_settings``
    // (Pydantic would silently drop unknown fields otherwise). Persist
    // them via the /gates/{key} endpoint. Omit the override entirely when
    // the input is blank so the engine falls back to its default.
    const minScore = this.toOptionalNumber(this.settings.min_score_for_trade);
    const minConfidence = this.toOptionalNumber(this.settings.min_confidence_for_trade);

    this.api.updateSettings(update).subscribe({
      next: () => {
        const gateCalls = [
          this.api.updateGate('min_score', minScore),
          this.api.updateGate('min_confidence', minConfidence),
        ];
        let remaining = gateCalls.length;
        let errored = false;
        gateCalls.forEach(obs =>
          obs.subscribe({
            next: () => {
              remaining -= 1;
              if (remaining === 0 && !errored) {
                this.saveMessage = 'Settings saved!';
                setTimeout(() => (this.saveMessage = ''), 3000);
              }
            },
            error: () => { errored = true; this.saveMessage = 'Error saving auto-trade thresholds'; },
          }),
        );
      },
      error: () => { this.saveMessage = 'Error saving settings'; }
    });
  }

  private toOptionalNumber(v: any): number | null {
    if (v === null || v === undefined || v === '') return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
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
