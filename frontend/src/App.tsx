import React, { useState, useEffect, lazy, Suspense } from 'react';
import { createHashRouter, RouterProvider, Outlet, Navigate } from 'react-router-dom';
import UnsavedGuard from './components/UnsavedGuard';
import PageError from './components/ui/PageError';
import Sidebar from './components/Sidebar';
import { LoginModal } from './components/LoginModal';
import './index.css';

const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const ClientsPage = lazy(() => import('./pages/ClientsPage'));
const NodesPage = lazy(() => import('./pages/NodesPage'));
const RoutingPage = lazy(() => import('./pages/RoutingPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
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
const EventsPage = lazy(() => import('./pages/EventsPage'));

const PageSkeleton: React.FC = () => (
  <div className="animate-pulse space-y-6">
    <div className="h-8 bg-neutral-200 rounded-lg w-1/4"></div>
    <div className="h-32 bg-neutral-200 rounded-xl"></div>
    <div className="h-64 bg-neutral-200 rounded-xl"></div>
  </div>
);

function Shell() {
  return <div className="app-shell"><UnsavedGuard /><Sidebar onLogout={() => {
    const detail = { cancelled: false }; window.dispatchEvent(new CustomEvent('ui:before-logout', { detail }));
    if (detail.cancelled) return;
    localStorage.removeItem('token'); window.dispatchEvent(new Event('auth:unauthorized'));
  }} /><main className="app-main" id="main-content"><Suspense fallback={<PageSkeleton />}><Outlet /></Suspense></main></div>;
}
const router = createHashRouter([{ element: <Shell />, children: [
  { path: '/', element: <DashboardPage /> }, { path: '/clients', element: <ClientsPage /> },
  { path: '/clients/:id/access', element: <ClientAccessPage /> }, { path: '/nodes', element: <NodesPage /> },
  { path: '/routing', element: <RoutingPage /> }, { path: '/warp', element: <WarpPage /> },
  { path: '/bypass', element: <BypassPage /> }, { path: '/protocols', element: <ProtocolsPage /> },
  { path: '/dns', element: <DnsPage /> }, { path: '/settings', element: <SettingsPage /> },
  { path: '/telegram', element: <TelegramPage /> }, { path: '/help', element: <HelpPage /> },
  { path: '/diagnostics', element: <DiagnosticsPage /> }, { path: '/history', element: <HistoryPage /> },
  { path: '/backups', element: <BackupsPage /> }, { path: '/events', element: <EventsPage /> },
  { path: '*', element: <Navigate to="/" replace /> },
].map(route => ({ ...route, errorElement: <PageError /> })) }]);

const App: React.FC = () => {
  const [authToken, setAuthToken] = useState<string | null>(() => localStorage.getItem('token'));

  useEffect(() => {
    const handleUnauthorized = () => {
      setAuthToken(null);
    };

    window.addEventListener('auth:unauthorized', handleUnauthorized);

    return () => {
      window.removeEventListener('auth:unauthorized', handleUnauthorized);
    };
  }, []);

  if (!authToken) {
    return <LoginModal onLoginSuccess={(token) => setAuthToken(token)} />;
  }

  return <RouterProvider router={router} />;
};

export default App;
