import React, { useEffect, useState } from 'react';
import {
  getMasterMappings, createMasterMapping, updateMasterMapping, deleteMasterMapping,
  getEngineMappings, createEngineMapping, updateEngineMapping, deleteEngineMapping,
} from '../api';
import { Search, Pencil, Trash2 } from 'lucide-react';
import ConfirmModal from './ConfirmModal';
import '../styles/MasterData.css';

// ── Model Codes Tab ───────────────────────────────────────────────────────────
function ModelCodesTab() {
  const [mappings, setMappings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');

  const [platformName, setPlatformName] = useState('');
  const [modelCode, setModelCode] = useState('');
  const [description, setDescription] = useState('');

  const [selectedIds, setSelectedIds] = useState(new Set());
  const [confirmConfig, setConfirmConfig] = useState(null);

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

  const openEdit = (item) => {
    setEditItem(item);
    setEditPlatform(item.platform_name);
    setEditCode(item.model_code);
    setEditDesc(item.description || '');
  };

  const closeEdit = () => setEditItem(null);

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
    <div className="master-layout">
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
                  <input type="checkbox" className="master-checkbox" checked={allVisibleSelected} onChange={toggleSelectAll} />
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
                    <td colSpan={4}>{platform} ({items.length})</td>
                  </tr>
                  {items.map(m => (
                    <tr key={m.id} className={selectedIds.has(m.id) ? 'master-row-selected' : ''}>
                      <td>
                        <input type="checkbox" className="master-checkbox" checked={selectedIds.has(m.id)} onChange={() => toggleSelect(m.id)} />
                      </td>
                      <td style={{ paddingLeft: 32 }}>{m.platform_name}</td>
                      <td><span className="model-code-badge">{m.model_code}</span></td>
                      <td>{m.description || 'No description'}</td>
                      <td style={{ textAlign: 'right' }}>
                        <div className="master-action-btns">
                          <button className="btn-icon-edit" onClick={() => openEdit(m)} title="Edit"><Pencil size={14} /></button>
                          <button className="btn-icon-delete" onClick={() => handleDelete(m.id)} title="Delete"><Trash2 size={14} /></button>
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

      <ConfirmModal
        isOpen={!!confirmConfig}
        title={confirmConfig?.title}
        message={confirmConfig?.message}
        confirmLabel={confirmConfig?.confirmLabel}
        onConfirm={confirmConfig?.onConfirm}
        onCancel={() => setConfirmConfig(null)}
      />

      {editItem && (
        <div className="modal-overlay" onClick={closeEdit}>
          <div className="modal-box" style={{ maxWidth: 520 }} onClick={e => e.stopPropagation()}>
            <h3 className="modal-title">Edit Mapping</h3>
            <div className="modal-fields">
              <div className="master-form-field">
                <label className="section-label">Platform Name</label>
                <input className="field-input" value={editPlatform} onChange={e => setEditPlatform(e.target.value)} placeholder="e.g. THAR ROXX" />
              </div>
              <div className="master-form-field">
                <label className="section-label">Model Code</label>
                <input className="field-input" value={editCode} onChange={e => setEditCode(e.target.value)} placeholder="e.g. AM4CRE..." />
              </div>
              <div className="master-form-field">
                <label className="section-label">Description</label>
                <input className="field-input" value={editDesc} onChange={e => setEditDesc(e.target.value)} placeholder="Brief description" />
              </div>
            </div>
            <div className="modal-actions">
              <button className="btn-secondary" onClick={closeEdit} disabled={editSaving}>Cancel</button>
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

// ── Engine Codes Tab ──────────────────────────────────────────────────────────
function EngineCodesTab() {
  const [mappings, setMappings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');

  const [sheetName, setSheetName] = useState('');
  const [partNo, setPartNo] = useState('');
  const [modelName, setModelName] = useState('');
  const [description, setDescription] = useState('');

  const [selectedIds, setSelectedIds] = useState(new Set());
  const [confirmConfig, setConfirmConfig] = useState(null);

  const [editItem, setEditItem] = useState(null);
  const [editSheet, setEditSheet] = useState('');
  const [editPartNo, setEditPartNo] = useState('');
  const [editModelName, setEditModelName] = useState('');
  const [editDesc, setEditDesc] = useState('');
  const [editSaving, setEditSaving] = useState(false);

  useEffect(() => { loadData(); }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const res = await getEngineMappings();
      setMappings(res.data);
    } catch (err) {
      console.error('Failed to load engine data', err);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    if (!sheetName.trim() || !partNo.trim()) {
      alert('Line / Sheet Name and Part No are required');
      return;
    }
    try {
      await createEngineMapping({
        sheet_name: sheetName.trim(),
        part_no: partNo.trim(),
        model_name: modelName.trim() || undefined,
        description: description.trim() || undefined,
      });
      setSheetName('');
      setPartNo('');
      setModelName('');
      setDescription('');
      loadData();
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to save engine mapping');
    }
  };

  const handleDelete = (id) => {
    setConfirmConfig({
      title: 'Delete Engine Mapping',
      message: 'This engine mapping will be permanently removed. This action cannot be undone.',
      confirmLabel: 'Delete',
      onConfirm: async () => {
        setConfirmConfig(null);
        try {
          await deleteEngineMapping(id);
          setSelectedIds(prev => { const n = new Set(prev); n.delete(id); return n; });
          loadData();
        } catch {
          alert('Failed to delete');
        }
      },
    });
  };

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
          await Promise.all([...selectedIds].map(id => deleteEngineMapping(id)));
          setSelectedIds(new Set());
          loadData();
        } catch {
          alert('Failed to delete some mappings');
          loadData();
        }
      },
    });
  };

  const openEdit = (item) => {
    setEditItem(item);
    setEditSheet(item.sheet_name);
    setEditPartNo(item.part_no);
    setEditModelName(item.model_name || '');
    setEditDesc(item.description || '');
  };

  const closeEdit = () => setEditItem(null);

  const handleEditSave = async () => {
    if (!editSheet.trim() || !editPartNo.trim()) {
      alert('Line / Sheet Name and Part No are required');
      return;
    }
    setEditSaving(true);
    try {
      await updateEngineMapping(editItem.id, {
        sheet_name: editSheet.trim(),
        part_no: editPartNo.trim(),
        model_name: editModelName.trim() || null,
        description: editDesc.trim() || null,
      });
      closeEdit();
      loadData();
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to update engine mapping');
    } finally {
      setEditSaving(false);
    }
  };

  const filteredMappings = mappings.filter(m =>
    m.part_no.toLowerCase().includes(searchTerm.toLowerCase()) ||
    m.sheet_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
    (m.model_name || '').toLowerCase().includes(searchTerm.toLowerCase()) ||
    (m.description || '').toLowerCase().includes(searchTerm.toLowerCase())
  );

  const groupedMappings = filteredMappings.reduce((acc, m) => {
    if (!acc[m.sheet_name]) acc[m.sheet_name] = [];
    acc[m.sheet_name].push(m);
    return acc;
  }, {});

  const allVisibleSelected = filteredMappings.length > 0 && selectedIds.size === filteredMappings.length;

  return (
    <div className="master-layout">
      <div className="master-form-panel">
        <h2 className="master-panel-title">Add New Engine Mapping</h2>
        <div className="master-form-grid">
          <div className="master-form-field">
            <label className="section-label">Line / Sheet Name</label>
            <input
              className="field-input"
              value={sheetName}
              onChange={e => setSheetName(e.target.value)}
              placeholder="e.g. D 25 LINE"
            />
          </div>
          <div className="master-form-field">
            <label className="section-label">Part No</label>
            <input
              className="field-input"
              value={partNo}
              onChange={e => setPartNo(e.target.value)}
              placeholder="e.g. 0301BAB04790N"
            />
          </div>
          <div className="master-form-field">
            <label className="section-label">Model Name</label>
            <input
              className="field-input"
              value={modelName}
              onChange={e => setModelName(e.target.value)}
              placeholder="e.g. LCCR SCORPIO"
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

      <div className="master-list-panel">
        <div className="master-list-head">
          <h2 className="master-list-title">
            Engine Mappings
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
                placeholder="Search part no, line..."
              />
            </div>
          </div>
        </div>

        {loading ? (
          <div className="master-loading">Loading...</div>
        ) : filteredMappings.length === 0 ? (
          <div className="master-empty">
            {searchTerm ? 'No engine mappings match your search.' : 'No engine mappings defined yet.'}
          </div>
        ) : (
          <table className="master-table">
            <thead>
              <tr>
                <th style={{ width: 40 }}>
                  <input type="checkbox" className="master-checkbox" checked={allVisibleSelected} onChange={toggleSelectAll} />
                </th>
                <th style={{ width: '20%' }}>Line</th>
                <th style={{ width: '22%' }}>Part No</th>
                <th style={{ width: '18%' }}>Model Name</th>
                <th>Description</th>
                <th style={{ textAlign: 'right', width: 120 }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(groupedMappings).map(([sheet, items]) => (
                <React.Fragment key={sheet}>
                  <tr className="master-group-row">
                    <td />
                    <td colSpan={5}>{sheet} ({items.length})</td>
                  </tr>
                  {items.map(m => (
                    <tr key={m.id} className={selectedIds.has(m.id) ? 'master-row-selected' : ''}>
                      <td>
                        <input type="checkbox" className="master-checkbox" checked={selectedIds.has(m.id)} onChange={() => toggleSelect(m.id)} />
                      </td>
                      <td style={{ paddingLeft: 32 }}>{m.sheet_name}</td>
                      <td><span className="model-code-badge">{m.part_no}</span></td>
                      <td>{m.model_name || '—'}</td>
                      <td>{m.description || 'No description'}</td>
                      <td style={{ textAlign: 'right' }}>
                        <div className="master-action-btns">
                          <button className="btn-icon-edit" onClick={() => openEdit(m)} title="Edit"><Pencil size={14} /></button>
                          <button className="btn-icon-delete" onClick={() => handleDelete(m.id)} title="Delete"><Trash2 size={14} /></button>
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

      <ConfirmModal
        isOpen={!!confirmConfig}
        title={confirmConfig?.title}
        message={confirmConfig?.message}
        confirmLabel={confirmConfig?.confirmLabel}
        onConfirm={confirmConfig?.onConfirm}
        onCancel={() => setConfirmConfig(null)}
      />

      {editItem && (
        <div className="modal-overlay" onClick={closeEdit}>
          <div className="modal-box" style={{ maxWidth: 520 }} onClick={e => e.stopPropagation()}>
            <h3 className="modal-title">Edit Engine Mapping</h3>
            <div className="modal-fields">
              <div className="master-form-field">
                <label className="section-label">Line / Sheet Name</label>
                <input className="field-input" value={editSheet} onChange={e => setEditSheet(e.target.value)} placeholder="e.g. D 25 LINE" />
              </div>
              <div className="master-form-field">
                <label className="section-label">Part No</label>
                <input className="field-input" value={editPartNo} onChange={e => setEditPartNo(e.target.value)} placeholder="e.g. 0301BAB04790N" />
              </div>
              <div className="master-form-field">
                <label className="section-label">Model Name</label>
                <input className="field-input" value={editModelName} onChange={e => setEditModelName(e.target.value)} placeholder="e.g. LCCR SCORPIO" />
              </div>
              <div className="master-form-field">
                <label className="section-label">Description</label>
                <input className="field-input" value={editDesc} onChange={e => setEditDesc(e.target.value)} placeholder="Brief description" />
              </div>
            </div>
            <div className="modal-actions">
              <button className="btn-secondary" onClick={closeEdit} disabled={editSaving}>Cancel</button>
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

// ── Main MasterData page with tabs ────────────────────────────────────────────
export default function MasterData() {
  const [activeTab, setActiveTab] = useState('model');

  return (
    <div className="page-container">
      <div className="page-header">
        <h1 className="page-title">
          Master <span className="gradient-text">Data</span>
        </h1>
        <p className="page-subtitle">Define global platform-to-model mappings and engine codes.</p>
      </div>

      <div className="master-tabs">
        <button
          className={`master-tab-btn ${activeTab === 'model' ? 'active' : ''}`}
          onClick={() => setActiveTab('model')}
        >
          Model Codes
        </button>
        <button
          className={`master-tab-btn ${activeTab === 'engine' ? 'active' : ''}`}
          onClick={() => setActiveTab('engine')}
        >
          Engine Codes
        </button>
      </div>

      {activeTab === 'model' ? <ModelCodesTab /> : <EngineCodesTab />}
    </div>
  );
}
