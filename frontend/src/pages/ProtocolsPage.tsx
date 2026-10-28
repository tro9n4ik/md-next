import ProtocolSettingsTab from '../components/ProtocolSettingsTab';

export default function ProtocolsPage() {
  return <div className="page-layout protocols-page"><header className="dashboard-heading"><div><h1>Протоколы</h1><p>Каналы подключения и параметры профилей</p></div></header><ProtocolSettingsTab /></div>;
}
