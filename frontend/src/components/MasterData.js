import React, { useEffect, useState } from 'react';
import { getMasterMappings, createMasterMapping, deleteMasterMapping } from '../api';
import { Search } from 'lucide-react';
import '../styles/MasterData.css';

export default function MasterData() {
  const [mappings, setMappings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');

  const [platformName, setPlatformName] = useState('');
  const [modelCode, setModelCode] = useState('');
  const [description, setDescription] = useState('');

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
    if (!platformName || !modelCode) {
      alert('Platform Name and Model Code are required');
      return;
    }
    try {
      await createMasterMapping({ platform_name: platformName, model_code: modelCode, description });
      setPlatformName('');
      setModelCode('');
      setDescription('');
      loadData();
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to save mapping');
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('Delete this mapping?')) return;
    try {
      await deleteMasterMapping(id);
      loadData();
    } catch {
      alert('Failed to delete');
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
                  <th style={{ width: '25%' }}>Platform</th>
                  <th style={{ width: '20%' }}>Model Code</th>
                  <th>Description</th>
                  <th style={{ textAlign: 'right', width: 100 }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(groupedMappings).map(([platform, items]) => (
                  <React.Fragment key={platform}>
                    <tr className="master-group-row">
                      <td colSpan={4}>
                        {platform} ({items.length})
                      </td>
                    </tr>
                    {items.map(m => (
                      <tr key={m.id}>
                        <td style={{ paddingLeft: 32 }}>{m.platform_name}</td>
                        <td>
                          <span className="model-code-badge">{m.model_code}</span>
                        </td>
                        <td>{m.description || 'No description'}</td>
                        <td style={{ textAlign: 'right' }}>
                          <button className="btn-danger" onClick={() => handleDelete(m.id)}>
                            Delete
                          </button>
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
    </div>
  );
}
