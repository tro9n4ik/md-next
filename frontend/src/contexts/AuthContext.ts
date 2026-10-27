import { createContext, useContext } from 'react';

export interface CurrentUser {
  id: number;
  username: string;
  role: 'admin' | 'subadmin';
  is_active: boolean;
  totp_enabled: boolean;
  view_all_clients: boolean;
  can_issue_subscriptions: boolean;
  can_delete_subscriptions: boolean;
  can_pause_subscriptions: boolean;
}

export const AuthContext = createContext<CurrentUser | null>(null);

export function useCurrentUser(): CurrentUser {
  const user = useContext(AuthContext);
  if (!user) throw new Error('Учётная запись не загружена');
  return user;
}
