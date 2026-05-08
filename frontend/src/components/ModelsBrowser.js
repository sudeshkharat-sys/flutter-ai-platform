import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Brain, Trash2, Plus, CheckCircle2 } from 'lucide-react';
import { getModels, deleteModel } from '../api';
import ConfirmModal from './ConfirmModal';
import '../styles/ModelsBrowser.css';

export default function ModelLibrary() {
  const [models, setModels] = useState([]);
  const [loading, setLoading] = useState(true);
  const [confirmConfig, setConfirmConfig] = useState(null);

  useEffect(() => {
    getModels()
      .then(r => {
        setModels(r.data.filter(m => m.status === 'ready'));
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const handleDelete = (id, name) => {
    setConfirmConfig({
      title: 'Delete Model',
      message: `"${name}" will be permanently removed from the library. This action cannot be undone.`,
      confirmLabel: 'Delete',
      onConfirm: () => {
        setConfirmConfig(null);
        deleteModel(id).then(() => setModels(m => m.filter(x => x.id !== id)));
      },
    });
  };

  return (
    <div className="page-container">
      <div className="models-page-head">
        <div>
          <h1 className="page-title">
            Model <span className="gradient-text">Library</span>
          </h1>
          <p className="page-subtitle">Browse and manage your converted AI model assets.</p>
        </div>
        <Link to="/new-app" className="btn-primary">
          <Plus size={15} />
          Convert New Model
        </Link>
      </div>

      {loading ? (
        <div className="models-loading">
          <div className="models-loading-spinner" />
          Loading models...
        </div>
      ) : models.length === 0 ? (
        <div className="models-empty">
          <div className="models-empty-icon">
            <Brain size={32} />
          </div>
          <div className="models-empty-title">No models yet</div>
          <p className="models-empty-desc">Convert a YOLO .pt model to get started.</p>
          <Link to="/new-app" className="btn-primary">
            <Plus size={15} />
            Convert First Model
          </Link>
        </div>
      ) : (
        <div className="models-grid">
          {models.map((model, idx) => (
            <div
              key={model.id}
              className="model-card"
              style={{ animationDelay: `${idx * 0.07}s` }}
            >
              <div className="model-card-header">
                <div className="model-card-icon">
                  <Brain size={20} />
                </div>
                <div className="model-card-meta">
                  <div className="model-card-title">{model.vision_project_name}</div>
                  <div className="model-card-count">{model.classes.length} detection classes</div>
                </div>
              </div>

              <div className="model-card-tags">
                {model.classes.slice(0, 5).map(c => (
                  <span key={c} className="model-tag">{c}</span>
                ))}
                {model.classes.length > 5 && (
                  <span className="model-tag model-tag-more">+{model.classes.length - 5}</span>
                )}
              </div>

              <div className="model-card-footer">
                <div className="model-status-badge">
                  <CheckCircle2 size={12} />
                  Ready
                </div>
                <button className="btn-danger" onClick={() => handleDelete(model.id, model.vision_project_name)}>
                  <Trash2 size={13} />
                  Delete
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
