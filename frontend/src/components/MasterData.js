import React, { useEffect, useState } from 'react';
import { getMasterMappings, createMasterMapping, updateMasterMapping, deleteMasterMapping } from '../api';
import { Search, Pencil, Trash2 } from 'lucide-react';
import ConfirmModal from './ConfirmModal';
import '../styles/MasterData.css';

export default function MasterData() {
  const [mappings, setMappings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');

  // Add form state
  const [platformName, setPlatformName] = useState('');
  const [modelCode, setModelCode] = useState('');
  const [description, setDescription] = useState('');

  // Multi-select delete state
  const [selectedIds, setSelectedIds] = useState(new Set());

  // Confirm modal state  { title, message, onConfirm }  or  null = closed
  const [confirmConfig, setConfirmConfig] = useState(null);

  // Edit modal state
  const [editItem, setEditItem] = useState(null);
  const [editPlatform, setEditPlatform] = useState('');
  const [editCode, setEditCode] = useState('');
  const [editDesc, setEditDesc] = useState('');
  const [editSaving, setEditSaving] = useState(false);

  useEffect(() => { loadData(); }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const gr = await getMasterMappings();
      setMappings(gr.data);
    } catch (err) {
      console.error('Failed to load master data', err);
    } finally {
      setLoading(false);
    }
  };

  // ── Add ──────────────────────────────────────────────────────────────────────
  const handleSave = async () => {
    if (!platformName.trim() || !modelCode.trim()) {
      alert('Platform Name and Model Code are required');
      return;
    }
    try {
      await createMasterMapping({
        platform_name: platformName.trim(),
        model_code: modelCode.trim(),
        description: description.trim() || undefined,
      });
      setPlatformName('');
      setModelCode('');
      setDescription('');
      loadData();
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to save mapping');
    }
  };

  // ── Single delete ─────────────────────────────────────────────────────────
  const handleDelete = (id) => {
    setConfirmConfig({
      title: 'Delete Mapping',
      message: 'This mapping will be permanently removed. This action cannot be undone.',
      confirmLabel: 'Delete',
      onConfirm: async () => {
        setConfirmConfig(null);
        try {
          await deleteMasterMapping(id);
          setSelectedIds(prev => { const n = new Set(prev); n.delete(id); return n; });
          loadData();
        } catch {
          alert('Failed to delete');
        }
      },
    });
  };

  // ── Multi-select ──────────────────────────────────────────────────────────
  const toggleSelect = (id) => {
    setSelectedIds(prev => {
      const n = new Set(prev);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });
  };

  const toggleSelectAll = () => {
    if (selectedIds.size === filteredMappings.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(filteredMappings.map(m => m.id)));
    }
  };

  const handleBulkDelete = () => {
    if (selectedIds.size === 0) return;
    setConfirmConfig({
      title: `Delete ${selectedIds.size} Mapping${selectedIds.size > 1 ? 's' : ''}`,
      message: `You are about to permanently delete ${selectedIds.size} selected mapping${selectedIds.size > 1 ? 's' : ''}. This action cannot be undone.`,
      confirmLabel: `Delete ${selectedIds.size}`,
      onConfirm: async () => {
        setConfirmConfig(null);
        try {
          await Promise.all([...selectedIds].map(id => deleteMasterMapping(id)));
          setSelectedIds(new Set());
          loadData();
        } catch {
          alert('Failed to delete some mappings');
          loadData();
        }
      },
    });
  };

  // ── Edit modal ────────────────────────────────────────────────────────────
  const openEdit = (item) => {
    setEditItem(item);
    setEditPlatform(item.platform_name);
    setEditCode(item.model_code);
    setEditDesc(item.description || '');
  };

  const closeEdit = () => {
    setEditItem(null);
  };

  const handleEditSave = async () => {
    if (!editPlatform.trim() || !editCode.trim()) {
      alert('Platform Name and Model Code are required');
      return;
    }
    setEditSaving(true);
    try {
      await updateMasterMapping(editItem.id, {
        platform_name: editPlatform.trim(),
        model_code: editCode.trim(),
        description: editDesc.trim() || null,
      });
      closeEdit();
      loadData();
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to update mapping');
    } finally {
      setEditSaving(false);
    }
  };

  // ── Filtered + grouped ────────────────────────────────────────────────────
  const filteredMappings = mappings.filter(m =>
    m.model_code.toLowerCase().includes(searchTerm.toLowerCase()) ||
    m.platform_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
    (m.description || '').toLowerCase().includes(searchTerm.toLowerCase())
  );

  const groupedMappings = filteredMappings.reduce((acc, m) => {
    if (!acc[m.platform_name]) acc[m.platform_name] = [];
    acc[m.platform_name].push(m);
    return acc;
  }, {});

  const allVisibleSelected = filteredMappings.length > 0 && selectedIds.size === filteredMappings.length;

  return (
    <div className="page-container">
      <div className="page-header">
        <h1 className="page-title">
          Master <span className="gradient-text">Data</span>
        </h1>
        <p className="page-subtitle">Define global platform-to-model mappings and codes.</p>
      </div>

      <div className="master-layout">

        {/* ── Add Form ────────────────────────────────────────── */}
        <div className="master-form-panel">
          <h2 className="master-panel-title">Add New Mapping</h2>
          <div className="master-form-grid">
            <div className="master-form-field">
              <label className="section-label">Platform Name</label>
              <input
                className="field-input"
                value={platformName}
                onChange={e => setPlatformName(e.target.value)}
                placeholder="e.g. THAR ROXX"
              />
            </div>
            <div className="master-form-field">
              <label className="section-label">Model Code</label>
              <input
                className="field-input"
                value={modelCode}
                onChange={e => setModelCode(e.target.value)}
                placeholder="e.g. AM4CRE..."
              />
            </div>
            <div className="master-form-field">
              <label className="section-label">Description</label>
              <input
                className="field-input"
                value={description}
                onChange={e => setDescription(e.target.value)}
                placeholder="Brief description"
              />
            </div>
            <button className="btn-primary" onClick={handleSave} style={{ height: 46, whiteSpace: 'nowrap' }}>
              Add Mapping
            </button>
          </div>
        </div>

        {/* ── List Table ──────────────────────────────────────── */}
        <div className="master-list-panel">
          <div className="master-list-head">
            <h2 className="master-list-title">
              Defined Mappings
              <span style={{ color: 'var(--text-muted)', fontWeight: 400, fontSize: 15, marginLeft: 8 }}>
                ({mappings.length})
              </span>
            </h2>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              {selectedIds.size > 0 && (
                <button className="btn-bulk-delete" onClick={handleBulkDelete}>
                  <Trash2 size={14} />
                  Delete Selected ({selectedIds.size})
                </button>
              )}
              <div className="master-search-wrap">
                <Search size={15} className="master-search-icon" />
                <input
                  className="master-search"
                  value={searchTerm}
                  onChange={e => setSearchTerm(e.target.value)}
                  placeholder="Search code, platform..."
                />
              </div>
            </div>
          </div>

          {loading ? (
            <div className="master-loading">Loading...</div>
          ) : filteredMappings.length === 0 ? (
            <div className="master-empty">
              {searchTerm ? 'No mappings match your search.' : 'No master mappings defined yet.'}
            </div>
          ) : (
            <table className="master-table">
              <thead>
                <tr>
                  <th style={{ width: 40 }}>
                    <input
                      type="checkbox"
                      className="master-checkbox"
                      checked={allVisibleSelected}
                      onChange={toggleSelectAll}
                    />
                  </th>
                  <th style={{ width: '25%' }}>Platform</th>
                  <th style={{ width: '20%' }}>Model Code</th>
                  <th>Description</th>
                  <th style={{ textAlign: 'right', width: 120 }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(groupedMappings).map(([platform, items]) => (
                  <React.Fragment key={platform}>
                    <tr className="master-group-row">
                      <td />
                      <td colSpan={4}>
                        {platform} ({items.length})
                      </td>
                    </tr>
                    {items.map(m => (
                      <tr key={m.id} className={selectedIds.has(m.id) ? 'master-row-selected' : ''}>
                        <td>
                          <input
                            type="checkbox"
                            className="master-checkbox"
                            checked={selectedIds.has(m.id)}
                            onChange={() => toggleSelect(m.id)}
                          />
                        </td>
                        <td style={{ paddingLeft: 32 }}>{m.platform_name}</td>
                        <td>
                          <span className="model-code-badge">{m.model_code}</span>
                        </td>
                        <td>{m.description || 'No description'}</td>
                        <td style={{ textAlign: 'right' }}>
                          <div className="master-action-btns">
                            <button className="btn-icon-edit" onClick={() => openEdit(m)} title="Edit">
                              <Pencil size={14} />
                            </button>
                            <button className="btn-icon-delete" onClick={() => handleDelete(m.id)} title="Delete">
                              <Trash2 size={14} />
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          )}
        </div>

      </div>

      {/* ── Confirm Modal ──────────────────────────────────────────────────── */}
      <ConfirmModal
        isOpen={!!confirmConfig}
        title={confirmConfig?.title}
        message={confirmConfig?.message}
        confirmLabel={confirmConfig?.confirmLabel}
        onConfirm={confirmConfig?.onConfirm}
        onCancel={() => setConfirmConfig(null)}
      />

      {/* ── Edit Modal ─────────────────────────────────────────────────────── */}
      {editItem && (
        <div className="modal-overlay" onClick={closeEdit}>
          <div className="modal-box" style={{ maxWidth: 520 }} onClick={e => e.stopPropagation()}>
            <h3 className="modal-title">Edit Mapping</h3>
            <div className="modal-fields">
              <div className="master-form-field">
                <label className="section-label">Platform Name</label>
                <input
                  className="field-input"
                  value={editPlatform}
                  onChange={e => setEditPlatform(e.target.value)}
                  placeholder="e.g. THAR ROXX"
                />
              </div>
              <div className="master-form-field">
                <label className="section-label">Model Code</label>
                <input
                  className="field-input"
                  value={editCode}
                  onChange={e => setEditCode(e.target.value)}
                  placeholder="e.g. AM4CRE..."
                />
              </div>
              <div className="master-form-field">
                <label className="section-label">Description</label>
                <input
                  className="field-input"
                  value={editDesc}
                  onChange={e => setEditDesc(e.target.value)}
                  placeholder="Brief description"
                />
              </div>
            </div>
            <div className="modal-actions">
              <button className="btn-secondary" onClick={closeEdit} disabled={editSaving}>
                Cancel
              </button>
              <button className="btn-primary" onClick={handleEditSave} disabled={editSaving}>
                {editSaving ? 'Saving...' : 'Save Changes'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
