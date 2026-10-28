import React, { useState, useEffect, lazy, Suspense } from 'react';
import { createHashRouter, RouterProvider, Outlet, Navigate } from 'react-router-dom';
import UnsavedGuard from './components/UnsavedGuard';
import PageError from './components/ui/PageError';
import Sidebar from './components/Sidebar';
import Topbar from './components/Topbar';
import ConfirmationProvider from './components/ui/ConfirmationProvider';
import { useConfirm } from './contexts/ConfirmationContext';
import { LoginModal } from './components/LoginModal';
import './index.css';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { AuthContext, useCurrentUser } from './contexts/AuthContext';
import type { CurrentUser } from './contexts/AuthContext';
import { apiFetch } from './utils/api';

const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const ClientsPage = lazy(() => import('./pages/ClientsPage'));
const NodesPage = lazy(() => import('./pages/NodesPage'));
const RoutingPage = lazy(() => import('./pages/RoutingPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
const GeneralSettingsPage = lazy(() => import('./pages/GeneralSettingsPage'));
const UpdatePage = lazy(() => import('./pages/UpdatePage'));
const PlaceholderPage = lazy(() => import('./pages/PlaceholderPage'));
const TelegramPage = lazy(() => import('./pages/TelegramPage'));
const WarpPage = lazy(() => import('./pages/WarpPage'));
const ProtocolsPage = lazy(() => import('./pages/ProtocolsPage'));
const DnsPage = lazy(() => import('./pages/DnsPage'));
const ClientAccessPage = lazy(() => import('./pages/ClientAccessPage'));
const BypassPage = lazy(() => import('./pages/BypassPage'));
const HelpPage = lazy(() => import('./pages/HelpPage'));
const DiagnosticsPage = lazy(() => import('./pages/DiagnosticsPage'));
const HistoryPage = lazy(() => import('./pages/HistoryPage'));
const BackupsPage = lazy(() => import('./pages/BackupsPage'));
const UsersPage = lazy(() => import('./pages/UsersPage'));
const AccountPage = lazy(() => import('./pages/AccountPage'));
const EventsPage = lazy(() => import('./pages/EventsPage'));

const PageSkeleton: React.FC = () => (
  <div className="animate-pulse space-y-6">
    <div className="h-8 bg-neutral-200 rounded-lg w-1/4"></div>
    <div className="h-32 bg-neutral-200 rounded-xl"></div>
    <div className="h-64 bg-neutral-200 rounded-xl"></div>
  </div>
);

function AdminOnly({ children }: { children: React.ReactNode }) {
  return useCurrentUser().role === "admin" ? children : <Navigate to="/clients" replace />;
}

function SettingsIndex() {
  return useCurrentUser().role === 'admin' ? <GeneralSettingsPage /> : <Navigate to="/settings/account" replace />;
}

function Shell() {
  return <ConfirmationProvider><ShellContent /></ConfirmationProvider>;
}
function ShellContent() {
  const confirm = useConfirm();
  return <div className="app-shell"><UnsavedGuard /><Sidebar onLogout={async () => {
    const detail = { cancelled: false, hasUnsavedChanges: false }; window.dispatchEvent(new CustomEvent('ui:before-logout', { detail }));
    if (detail.cancelled) return;
    if (detail.hasUnsavedChanges && !await confirm({ title: 'Выйти из панели?', description: 'Есть несохранённые изменения. При выходе они будут потеряны.', confirmText: 'Выйти без сохранения', cancelText: 'Остаться', tone: 'warning' })) return;
    localStorage.removeItem('token'); window.dispatchEvent(new Event('auth:unauthorized'));
  }} /><div className="app-workspace"><Topbar /><main className="app-main" id="main-content"><Suspense fallback={<PageSkeleton />}><Outlet /></Suspense></main></div></div>;
}
const router = createHashRouter([{ element: <Shell />, children: [
  { path: '/account', element: <Navigate to="/settings/account" replace /> },
  { path: '/', element: <DashboardPage /> }, { path: '/clients', element: <ClientsPage /> },
  { path: '/clients/:id/access', element: <ClientAccessPage /> }, { path: '/nodes', element: <NodesPage /> },
  { path: '/routing', element: <RoutingPage /> }, { path: '/warp', element: <WarpPage /> },
  { path: '/bypass', element: <BypassPage /> }, { path: '/protocols', element: <ProtocolsPage /> },
  { path: '/dns', element: <DnsPage /> }, { path: '/settings', element: <SettingsPage />, children: [
    { index: true, element: <SettingsIndex /> },
    { path: 'account', element: <AccountPage embedded /> },
    ...[
      { path: 'users', element: <UsersPage /> },
      { path: 'telegram', element: <TelegramPage embedded /> },
      { path: 'backups', element: <BackupsPage embedded /> },
      { path: 'update', element: <UpdatePage /> },
      { path: 'placeholder', element: <PlaceholderPage /> },
      { path: 'diagnostics', element: <DiagnosticsPage embedded /> },
      { path: 'history', element: <HistoryPage embedded /> },
      { path: 'events', element: <EventsPage embedded /> },
    ].map(route => ({...route, element: <AdminOnly>{route.element}</AdminOnly>})),
  ] },
  { path: '/telegram', element: <Navigate to="/settings/telegram" replace /> }, { path: '/wiki', element: <HelpPage /> },
  { path: '/help', element: <Navigate to="/wiki" replace /> },
  { path: '/diagnostics', element: <Navigate to="/settings/diagnostics" replace /> }, { path: '/history', element: <Navigate to="/settings/history" replace /> },
  { path: '/backups', element: <Navigate to="/settings/backups" replace /> }, { path: '/events', element: <Navigate to="/settings/events" replace /> },
  { path: '*', element: <Navigate to="/" replace /> },
].map(route => ({ ...route, element: route.path === '/account' || route.path === '/settings' || route.path?.startsWith('/clients') ? route.element : <AdminOnly>{route.element}</AdminOnly>, errorElement: <PageError /> })) }]);

const App: React.FC = () => {
  const queryClient = useQueryClient();
  const [authToken, setAuthToken] = useState<string | null>(() => localStorage.getItem('token'));

  const account = useQuery<CurrentUser>({
    queryKey: ['currentUser', authToken], enabled: !!authToken, retry: false,
    queryFn: async () => {
      const response = await apiFetch('/api/v1/auth/me');
      if (!response.ok) throw new Error('Не удалось проверить права доступа');
      return response.json();
    },
  });

  useEffect(() => {
    const handleUnauthorized = () => {
      setAuthToken(null);
      queryClient.clear();
    };

    window.addEventListener('auth:unauthorized', handleUnauthorized);

    return () => {
      window.removeEventListener('auth:unauthorized', handleUnauthorized);
    };
  }, [queryClient]);

  if (!authToken) {
    return <LoginModal onLoginSuccess={(token) => { queryClient.clear(); setAuthToken(token); }} />;
  }

  if (account.isLoading) return <div className="p-8"><PageSkeleton /></div>;
  if (!account.data || account.isError) return <PageError />;
  return <AuthContext.Provider value={account.data}><RouterProvider router={router} /></AuthContext.Provider>;
};

export default App;
