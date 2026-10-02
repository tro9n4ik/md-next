import React, { useState, useEffect, lazy, Suspense } from 'react';
import { HashRouter, Routes, Route, Navigate } from 'react-router-dom';
import Sidebar from './components/Sidebar';
import { LoginModal } from './components/LoginModal';
import './index.css';

const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const ClientsPage = lazy(() => import('./pages/ClientsPage'));
const NodesPage = lazy(() => import('./pages/NodesPage'));
const RoutingPage = lazy(() => import('./pages/RoutingPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
const WarpPage = lazy(() => import('./pages/WarpPage'));
const ProtocolsPage = lazy(() => import('./pages/ProtocolsPage'));
const DnsPage = lazy(() => import('./pages/DnsPage'));
const ClientAccessPage = lazy(() => import('./pages/ClientAccessPage'));

const PageSkeleton: React.FC = () => (
  <div className="animate-pulse space-y-6">
    <div className="h-8 bg-neutral-200 rounded-lg w-1/4"></div>
    <div className="h-32 bg-neutral-200 rounded-xl"></div>
    <div className="h-64 bg-neutral-200 rounded-xl"></div>
  </div>
);

const App: React.FC = () => {
  const [authToken, setAuthToken] = useState<string | null>(null);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (token) {
      setAuthToken(token);
    }

    const handleUnauthorized = () => {
      setAuthToken(null);
    };

    window.addEventListener('auth:unauthorized', handleUnauthorized);

    return () => {
      window.removeEventListener('auth:unauthorized', handleUnauthorized);
    };
  }, []);

  const handleLogout = () => {
    localStorage.removeItem('token');
    setAuthToken(null);
  };

  if (!authToken) {
    return <LoginModal onLoginSuccess={(token) => setAuthToken(token)} />;
  }

  return (
    <HashRouter>
      <div className="flex min-h-screen bg-[#f4f5f7]">
        <Sidebar onLogout={handleLogout} />
        <main className="flex-1 p-4 lg:p-8 overflow-y-auto min-w-0">
          <Suspense fallback={<PageSkeleton />}>
            <Routes>
              <Route path="/" element={<DashboardPage />} />
              <Route path="/clients" element={<ClientsPage />} />
              <Route path="/clients/:id/access" element={<ClientAccessPage />} />
              <Route path="/nodes" element={<NodesPage />} />
              <Route path="/routing" element={<RoutingPage />} />
              <Route path="/warp" element={<WarpPage />} />
              <Route path="/protocols" element={<ProtocolsPage />} />
              <Route path="/dns" element={<DnsPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Suspense>
        </main>
      </div>
    </HashRouter>
  );
};

export default App;
