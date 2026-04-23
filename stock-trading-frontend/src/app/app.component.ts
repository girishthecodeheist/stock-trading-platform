import { Component, OnInit, OnDestroy } from '@angular/core';
import { RouterOutlet, RouterLink, RouterLinkActive, Router } from '@angular/router';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from './services/api.service';
import { ToastComponent } from './components/toast/toast.component';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, RouterLink, RouterLinkActive, CommonModule, FormsModule, ToastComponent],
  template: `
    <div class="app-layout">
      <nav class="sidebar glass-card">
        <div class="sidebar-logo">
          <span class="logo-icon">&#x1F525;</span>
          <span class="logo-text">NSE AutoTrade</span>
        </div>

        <!-- Trade Mode Selector -->
        <div class="trade-mode-selector" [class.live-mode]="tradeMode === 'LIVE'">
          <div class="mode-indicator" [class.paper]="tradeMode==='PAPER'" [class.live]="tradeMode==='LIVE'">
            <span class="pulse-dot"></span>
            {{ tradeMode === 'LIVE' ? 'LIVE TRADING' : 'PAPER TRADING' }}
          </div>
          <select [(ngModel)]="tradeMode" (change)="onModeChange()" class="mode-select">
            <option value="PAPER">Paper Trade (Simulated)</option>
            <option value="LIVE">Live Trade (Real Money)</option>
          </select>
        </div>

        <div class="nav-links">
          <a routerLink="/dashboard" routerLinkActive="active" class="nav-item">
            <span class="nav-icon">&#x1F3E0;</span>
            <span class="nav-label">Dashboard</span>
          </a>
          <a routerLink="/chart" routerLinkActive="active" class="nav-item">
            <span class="nav-icon">&#x1F4CA;</span>
            <span class="nav-label">Chart</span>
          </a>
          <a routerLink="/trades" routerLinkActive="active" class="nav-item">
            <span class="nav-icon">&#x1F4B0;</span>
            <span class="nav-label">Trades</span>
          </a>
          <a routerLink="/signals" routerLinkActive="active" class="nav-item">
            <span class="nav-icon">&#x1F4E1;</span>
            <span class="nav-label">Signals</span>
          </a>
          <a routerLink="/heatmap" routerLinkActive="active" class="nav-item">
            <span class="nav-icon">&#x1F5FA;</span>
            <span class="nav-label">Heatmap</span>
          </a>
          <a routerLink="/analytics" routerLinkActive="active" class="nav-item">
            <span class="nav-icon">&#x1F4C9;</span>
            <span class="nav-label">Analytics</span>
          </a>
          <a routerLink="/trade-journal" routerLinkActive="active" class="nav-item">
            <span class="nav-icon">&#x1F4DD;</span>
            <span class="nav-label">Journal</span>
          </a>
          <a routerLink="/news" routerLinkActive="active" class="nav-item">
            <span class="nav-icon">&#x1F4F0;</span>
            <span class="nav-label">News</span>
          </a>
          <a routerLink="/settings" routerLinkActive="active" class="nav-item">
            <span class="nav-icon">&#x2699;</span>
            <span class="nav-label">Settings</span>
          </a>
        </div>
        <div class="fyers-status" (click)="onFyersClick()">
          <div class="status-dot" [class.connected]="fyersConnected" [class.disconnected]="!fyersConnected"></div>
          <span class="status-text">{{ fyersConnected ? 'Fyers Live' : 'Connect Fyers' }}</span>
        </div>
        <div class="sidebar-footer">
          <span class="version">v3.0</span>
        </div>
      </nav>
      <main class="main-content">
        <router-outlet></router-outlet>
      </main>
    </div>
    <app-toast></app-toast>

    <!-- Live Mode Confirmation Dialog -->
    <div class="modal-overlay" *ngIf="showLiveConfirm" (click)="cancelLiveMode()">
      <div class="modal-card glass-card" (click)="$event.stopPropagation()">
        <h3>Switch to LIVE Trading?</h3>
        <p>This will place REAL orders with REAL money on Fyers. Are you sure?</p>
        <div class="modal-actions">
          <button class="btn-cancel" (click)="cancelLiveMode()">Stay in Paper Mode</button>
          <button class="btn-confirm-live" (click)="confirmLiveMode()">Yes, switch to LIVE</button>
        </div>
      </div>
    </div>
  `,
  styleUrl: './app.component.scss'
})
export class AppComponent implements OnInit, OnDestroy {
  fyersConnected = false;
  tradeMode: string = 'PAPER';
  showLiveConfirm = false;
  private statusInterval: any;

  constructor(private api: ApiService, private router: Router) {}

  ngOnInit() {
    this.checkFyersStatus();
    this.loadTradeMode();
    this.statusInterval = setInterval(() => this.checkFyersStatus(), 30000);
  }

  ngOnDestroy() {
    if (this.statusInterval) clearInterval(this.statusInterval);
  }

  checkFyersStatus() {
    this.api.getFyersStatus().subscribe({
      next: (res) => { this.fyersConnected = res.authenticated === true; },
      error: () => { this.fyersConnected = false; }
    });
  }

  loadTradeMode() {
    this.api.getTradeMode().subscribe({
      next: (res) => { this.tradeMode = res.mode || 'PAPER'; },
      error: () => { this.tradeMode = 'PAPER'; }
    });
  }

  onModeChange() {
    if (this.tradeMode === 'LIVE') {
      this.showLiveConfirm = true;
    } else {
      this.api.setTradeMode('PAPER').subscribe();
    }
  }

  confirmLiveMode() {
    this.showLiveConfirm = false;
    this.api.setTradeMode('LIVE').subscribe();
  }

  cancelLiveMode() {
    this.showLiveConfirm = false;
    this.tradeMode = 'PAPER';
  }

  onFyersClick() {
    if (this.fyersConnected) return;
    this.api.getFyersAuthUrl().subscribe({
      next: (res) => {
        if (res.auth_url) {
          window.open(res.auth_url, '_blank');
        }
      }
    });
  }
}
