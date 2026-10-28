import { createContext, useContext } from 'react';

export type ConfirmationOptions = {
  title: string;
  description: string;
  confirmText?: string;
  cancelText?: string;
  tone?: 'default' | 'warning' | 'danger';
};
export type Confirm = (options: ConfirmationOptions) => Promise<boolean>;

// Без открытого интерфейса подтверждения действие не выполняется.
export const ConfirmationContext = createContext<Confirm>(() => Promise.resolve(false));
export const useConfirm = () => useContext(ConfirmationContext);
