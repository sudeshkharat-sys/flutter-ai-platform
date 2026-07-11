import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { createApp, buildAPK } from '../api';
import logo from '../DigiOcr.png';

const C = {
  surface: 'var(--surface)',
  accent: 'var(--accent)',
  border: 'var(--border)',
  text: 'var(--text)',
  muted: 'var(--text-muted)',
};

const inputStyle = { width: '100%', padding: '10px 14px', borderRadius: 8, border: `1px solid ${C.border}`, background: 'black', color: C.text, fontSize: 14 };
const labelStyle = { display: 'block', color: C.muted, fontSize: 11, fontWeight: 700, marginBottom: 6, textTransform: 'uppercase' };

export default function DigiOcr() {
  const navigate = useNavigate();
  const [appName, setAppName] = useState('');
  const [threshold, setThreshold] = useState(0.5);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState('');

  const handleBuild = async () => {
    if (!appName.trim()) {
      setError('Please enter an app name.');
      return;
    }
    setError('');
    setCreating(true);
    try {
      const res = await createApp({
        name: appName.trim(),
        model_asset_ids: [],
        inspection_tasks: [],
        app_settings: {
          app_mode: 'digi_ocr',
          full_ocr_threshold: threshold,
        },
      });
      const appId = res.data.id;
      await buildAPK(appId);
      navigate(`/apps/${appId}`);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to create Digi OCR app.');
      setCreating(false);
    }
  };

  return (
    <div style={{ maxWidth: 560, margin: '48px auto', padding: '0 20px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 24 }}>
        <img src={logo} alt="Digi OCR" style={{ width: 56, height: 56, borderRadius: 12, objectFit: 'contain', background: '#fff' }} />
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 800, margin: 0 }}>Digi OCR</h1>
          <p style={{ color: C.muted, fontSize: 13, margin: '4px 0 0' }}>
            A simple standalone app: scan a label and read every character on it, top-to-bottom and left-to-right.
          </p>
        </div>
      </div>

      <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 16, padding: 24, display: 'flex', flexDirection: 'column', gap: 18 }}>
        <div>
          <label style={labelStyle}>App Name</label>
          <input
            style={inputStyle}
            value={appName}
            onChange={e => setAppName(e.target.value)}
            placeholder="e.g. Digi OCR Scanner"
          />
        </div>

        <div>
          <label style={labelStyle}>Low-confidence threshold ({Math.round(threshold * 100)}%)</label>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={threshold}
            onChange={e => setThreshold(parseFloat(e.target.value))}
            style={{ width: '100%' }}
          />
          <span style={{ fontSize: 12, color: C.muted }}>
            Words recognized below this confidence are shown in red; the rest are shown in black.
          </span>
        </div>

        {error && <div style={{ color: '#ff6b6b', fontSize: 13 }}>{error}</div>}

        <button
          onClick={handleBuild}
          disabled={creating}
          style={{
            width: '100%',
            padding: '14px',
            borderRadius: 12,
            border: 'none',
            background: creating ? C.border : 'linear-gradient(135deg, var(--accent), var(--accent2))',
            color: '#fff',
            fontWeight: 800,
            fontSize: 15,
            cursor: creating ? 'default' : 'pointer',
          }}
        >
          {creating ? 'Starting Build...' : 'Build Digi OCR APK'}
        </button>
      </div>
    </div>
  );
}
