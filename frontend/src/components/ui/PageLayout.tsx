import type { ReactNode } from 'react';
import { ChevronRight } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

type PageLayoutProps = {
  title: string;
  description: string;
  icon: LucideIcon;
  children: ReactNode;
  actions?: ReactNode;
};

/** Общая ширина, заголовок и вертикальный ритм всех страниц панели. */
export default function PageLayout({ title, description, icon: Icon, children, actions }: PageLayoutProps) {
  return <div className="page-layout">
    <header className="page-header">
      <div className="page-breadcrumb" aria-label="Текущий раздел">
        <span>MD-Next</span><ChevronRight size={12} aria-hidden="true" /><span>{title}</span>
      </div>
      <div className="page-heading-row">
        <div className="page-heading">
          <div className="page-icon"><Icon size={23} strokeWidth={1.7} aria-hidden="true" /></div>
          <div className="min-w-0"><h1>{title}</h1><p>{description}</p></div>
        </div>
        {actions && <div className="page-actions">{actions}</div>}
      </div>
    </header>
    <div className="page-content">{children}</div>
  </div>;
}
