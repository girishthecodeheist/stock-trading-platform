import { Injectable } from '@angular/core';
import { Subject } from 'rxjs';

export interface ToastMessage {
  type: 'success' | 'error' | 'info' | 'warning';
  title: string;
  message: string;
  duration?: number;
  details?: string;
}

@Injectable({ providedIn: 'root' })
export class ToastService {
  private toastSubject = new Subject<ToastMessage>();
  toast$ = this.toastSubject.asObservable();

  success(title: string, message: string, details?: string) {
    this.toastSubject.next({ type: 'success', title, message, duration: 4000, details });
  }

  error(title: string, message: string, details?: string) {
    this.toastSubject.next({ type: 'error', title, message, duration: 6000, details });
  }

  info(title: string, message: string, details?: string) {
    this.toastSubject.next({ type: 'info', title, message, duration: 4000, details });
  }

  warning(title: string, message: string, details?: string) {
    this.toastSubject.next({ type: 'warning', title, message, duration: 5000, details });
  }
}
