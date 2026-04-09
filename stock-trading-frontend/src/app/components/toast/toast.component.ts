import { Component, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ToastService, ToastMessage } from '../../services/toast.service';
import { Subscription } from 'rxjs';

@Component({
  selector: 'app-toast',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="toast-container">
      <div *ngFor="let toast of toasts; let i = index"
           class="toast-item" [ngClass]="'toast-' + toast.type"
           (click)="dismiss(i)">
        <div class="toast-icon">
          <span *ngIf="toast.type === 'success'">&#x2714;</span>
          <span *ngIf="toast.type === 'error'">&#x2716;</span>
          <span *ngIf="toast.type === 'info'">&#x2139;</span>
          <span *ngIf="toast.type === 'warning'">&#x26A0;</span>
        </div>
        <div class="toast-content">
          <div class="toast-title">{{ toast.title }}</div>
          <div class="toast-message">{{ toast.message }}</div>
          <div class="toast-details" *ngIf="toast.details">{{ toast.details }}</div>
        </div>
        <button class="toast-close" (click)="dismiss(i); $event.stopPropagation()">&#x2715;</button>
      </div>
    </div>
  `,
  styles: [`
    .toast-container {
      position: fixed;
      top: 20px;
      right: 20px;
      z-index: 10000;
      display: flex;
      flex-direction: column;
      gap: 10px;
      max-width: 420px;
      width: 100%;
    }
    .toast-item {
      display: flex;
      align-items: flex-start;
      gap: 12px;
      padding: 14px 16px;
      border-radius: 12px;
      backdrop-filter: blur(20px);
      border: 1px solid rgba(255,255,255,0.1);
      cursor: pointer;
      animation: slideIn 0.3s ease-out;
      box-shadow: 0 8px 32px rgba(0,0,0,0.4);
    }
    @keyframes slideIn {
      from { transform: translateX(100%); opacity: 0; }
      to { transform: translateX(0); opacity: 1; }
    }
    .toast-success {
      background: linear-gradient(135deg, rgba(0,200,83,0.15), rgba(0,200,83,0.05));
      border-color: rgba(0,200,83,0.3);
    }
    .toast-error {
      background: linear-gradient(135deg, rgba(255,61,87,0.15), rgba(255,61,87,0.05));
      border-color: rgba(255,61,87,0.3);
    }
    .toast-info {
      background: linear-gradient(135deg, rgba(33,150,243,0.15), rgba(33,150,243,0.05));
      border-color: rgba(33,150,243,0.3);
    }
    .toast-warning {
      background: linear-gradient(135deg, rgba(255,193,7,0.15), rgba(255,193,7,0.05));
      border-color: rgba(255,193,7,0.3);
    }
    .toast-icon {
      font-size: 20px;
      line-height: 1;
      flex-shrink: 0;
    }
    .toast-success .toast-icon { color: #00c853; }
    .toast-error .toast-icon { color: #ff3d57; }
    .toast-info .toast-icon { color: #2196f3; }
    .toast-warning .toast-icon { color: #ffc107; }
    .toast-content {
      flex: 1;
      min-width: 0;
    }
    .toast-title {
      font-weight: 600;
      font-size: 14px;
      color: #fff;
      margin-bottom: 2px;
    }
    .toast-message {
      font-size: 13px;
      color: rgba(255,255,255,0.8);
      line-height: 1.4;
    }
    .toast-details {
      font-size: 11px;
      color: rgba(255,255,255,0.5);
      margin-top: 4px;
      font-family: monospace;
    }
    .toast-close {
      background: none;
      border: none;
      color: rgba(255,255,255,0.5);
      cursor: pointer;
      font-size: 14px;
      padding: 0;
      flex-shrink: 0;
    }
    .toast-close:hover { color: #fff; }
  `]
})
export class ToastComponent implements OnInit, OnDestroy {
  toasts: ToastMessage[] = [];
  private sub!: Subscription;

  constructor(private toastService: ToastService) {}

  ngOnInit() {
    this.sub = this.toastService.toast$.subscribe(toast => {
      this.toasts.push(toast);
      setTimeout(() => {
        const idx = this.toasts.indexOf(toast);
        if (idx >= 0) this.toasts.splice(idx, 1);
      }, toast.duration || 4000);
    });
  }

  ngOnDestroy() {
    if (this.sub) this.sub.unsubscribe();
  }

  dismiss(index: number) {
    this.toasts.splice(index, 1);
  }
}
