import React, { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Plus, Trash2, ClipboardList } from 'lucide-react';
import { getApps, deleteApp, exportApp } from '../api';
import ConfirmModal from './ConfirmModal';
import '../styles/Dashboard.css';

function Skeleton({ w = '100%', h = 16 }) {
  return <div className="skeleton" style={{ width: w, height: h }} />;
}

export default function Dashboard() {
  const [apps, setApps] = useState([]);
  const [loading, setLoading] = useState(true);
  const [confirmConfig, setConfirmConfig] = useState(null);
  const navigate = useNavigate();

  useEffect(() => {
    getApps()
      .then(r => { setApps(r.data); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  const handleDelete = (id, name) => {
    setConfirmConfig({
      title: 'Delete App',
      message: `"${name}" will be permanently deleted. This action cannot be undone.`,
      confirmLabel: 'Delete',
      onConfirm: async () => {
        setConfirmConfig(null);
        await deleteApp(id);
        setApps(prev => prev.filter(a => a.id !== id));
      },
    });
  };

  const handleExport = async (id, name) => {
    try {
      const r = await exportApp(id);
      const url = URL.createObjectURL(r.data);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${name.toLowerCase().replace(/ /g, '_')}_inspection_app.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      alert('Export failed. Make sure the backend is running.');
    }
  };

  return (
    <div className="dash-body">
      <div className="dash-section-head">
        <div>
          <h1 className="page-title" style={{ fontSize: 28, marginBottom: 4 }}>
            Inspection <span className="gradient-text">Apps</span>
          </h1>
          <p style={{ color: 'var(--text-muted)', fontSize: 14 }}>
            {loading ? '' : `${apps.length} app${apps.length !== 1 ? 's' : ''} total`}
          </p>
        </div>
        <div className="dash-actions-row">
          <Link to="/new-app" className="btn-primary">
            <Plus size={15} />
            New App
          </Link>
        </div>
      </div>

      {/* Loading Skeletons */}
      {loading && (
        <div className="dash-skeleton-grid">
          {[1, 2, 3].map(i => (
            <div key={i} className="dash-skeleton-card">
              <div style={{ display: 'flex', gap: 12, marginBottom: 8 }}>
                <Skeleton w={44} h={44} />
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <Skeleton h={18} w="60%" />
                  <Skeleton h={12} w="40%" />
                </div>
              </div>
              <Skeleton h={38} />
            </div>
          ))}
        </div>
      )}

      {/* Empty State */}
      {!loading && apps.length === 0 && (
        <div className="dash-empty">
          <div className="dash-empty-icon">
            <ClipboardList size={36} />
          </div>
          <div className="dash-empty-title">No inspection apps yet</div>
          <p className="dash-empty-desc">
            Start by creating an inspection workflow<br />for your vehicle models.
          </p>
          <Link to="/new-app" className="btn-primary">
            <Plus size={18} />
            Get Started
          </Link>
        </div>
      )}

      {/* App Grid */}
      {!loading && apps.length > 0 && (
        <div className="dash-grid">
          {apps.map((app, idx) => (
            <div
              key={app.id}
              className="app-card"
              style={{ animationDelay: `${idx * 0.07}s` }}
            >
              <div className="app-card-head">
                <div className="app-card-icon">
                  <ClipboardList size={22} color="var(--accent2)" />
                </div>
                <div className="app-card-meta">
                  <div className="app-card-name">{app.name}</div>
                  <div className="app-card-pkg">{app.package_name}</div>
                </div>
              </div>

              <div className="app-card-date">
                Created {new Date(app.updated_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
              </div>

              <div className="app-card-actions">
                <button
                  className="app-card-open-btn"
                  onClick={() => navigate(`/apps/${app.id}`)}
                >
                  Open Build Console
                </button>
                <button
                  className="app-card-delete-btn"
                  onClick={() => handleDelete(app.id, app.name)}
                  title="Delete app"
                >
                  <Trash2 size={16} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <ConfirmModal
        isOpen={!!confirmConfig}
        title={confirmConfig?.title}
        message={confirmConfig?.message}
        confirmLabel={confirmConfig?.confirmLabel}
        onConfirm={confirmConfig?.onConfirm}
        onCancel={() => setConfirmConfig(null)}
      />
    </div>
  );
}
