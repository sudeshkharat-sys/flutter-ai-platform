import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { getApp, getModels, buildAPK, downloadAPK, apkUrl, downloadPcPack, updateApp, uploadModel, getModelStatus, extractClasses, createApp, getMasterMappings, getEngineMappings, uploadReferenceImage, getReferenceImageUrl, uploadOcrModel, replaceModelPt } from '../api';
import ConfirmModal from './ConfirmModal';

const C = {
  surface: 'var(--surface)', surface2: 'var(--surface2)',
  accent: 'var(--accent)', success: 'var(--success)',
  border: 'var(--border)', text: 'var(--text)', muted: 'var(--text-muted)',
  error: 'var(--error)'
};

const inputStyle = { width: '100%', padding: '10px 14px', borderRadius: 8, border: `1px solid ${C.border}`, background: "black", color: C.text, fontSize: 14 };
const labelStyle = { display: 'block', color: C.muted, fontSize: 11, fontWeight: 700, marginBottom: 6, textTransform: 'uppercase' };
// Small toggle button used for the per-class Mandatory/Ignore rule pickers.
const classRuleButtonStyle = (active, activeColor) => ({
  padding: '3px 9px',
  borderRadius: 6,
  fontSize: 10,
  fontWeight: 700,
  cursor: 'pointer',
  border: `1px solid ${active ? activeColor : C.border}`,
  background: active ? activeColor : 'transparent',
  color: active ? '#fff' : C.muted,
});

// Type-to-filter replacement for a plain <select> (long model lists).
function SearchableSelect({ value, options, onChange, placeholder = 'Select...', style }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const boxRef = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);
  const selected = options.find(o => o.value === value);
  const needle = q.trim().toLowerCase();
  const shown = needle ? options.filter(o => o.label.toLowerCase().includes(needle)) : options;
  return (
    <div ref={boxRef} style={{ position: 'relative', width: '100%' }}>
      <div onClick={() => { setOpen(o => !o); setQ(''); }}
        style={{ ...inputStyle, ...(style || {}), cursor: 'pointer', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: selected ? C.text : C.muted }}>
        {selected ? selected.label : placeholder}
      </div>
      {open && (
        <div style={{ position: 'absolute', top: '100%', left: 0, minWidth: '100%', width: 'max-content', maxWidth: 420, zIndex: 1000, marginTop: 4, background: 'black', border: `1px solid ${C.border}`, borderRadius: 8, boxShadow: '0 8px 24px rgba(0,0,0,0.6)' }}>
          <input autoFocus value={q} onChange={e => setQ(e.target.value)} placeholder="Search..."
            onKeyDown={e => { if (e.key === 'Escape') setOpen(false); if (e.key === 'Enter' && shown.length === 1) { onChange(shown[0].value); setOpen(false); } }}
            style={{ ...inputStyle, borderRadius: '8px 8px 0 0', borderWidth: '0 0 1px 0' }} />
          <div style={{ maxHeight: 240, overflowY: 'auto' }}>
            <div onClick={() => { onChange(''); setOpen(false); }} style={{ padding: '8px 12px', cursor: 'pointer', color: C.muted, fontSize: 13 }}>{placeholder}</div>
            {shown.map(o => (
              <div key={o.value} onClick={() => { onChange(o.value); setOpen(false); }}
                style={{ padding: '8px 12px', cursor: 'pointer', fontSize: 13, color: C.text, background: o.value === value ? 'rgba(220, 20, 60, 0.15)' : 'transparent' }}>
                {o.label}
              </div>
            ))}
            {shown.length === 0 && <div style={{ padding: '8px 12px', color: C.muted, fontSize: 13 }}>No match</div>}
          </div>
        </div>
      )}
    </div>
  );
}

// When the model on a task changes, keep the class rules (mandatory / ignored /
// OCR config) for every class name the new model also has; drop only the rest.
function carryOverClassRules(task, newModel) {
  const classes = newModel?.classes || [];
  const has = (c) => classes.includes(c);
  const cfg = {};
  Object.entries(task.classOcrConfig || {}).forEach(([k, v]) => { if (has(k)) cfg[k] = v; });
  task.class = has(task.class) ? task.class : (classes[0] || '');
  task.mandatoryClasses = (task.mandatoryClasses || []).filter(has);
  task.ignoredClasses = (task.ignoredClasses || []).filter(has);
  task.classOcrConfig = cfg;
}

const taskSig = (t) => `${t.modelId}|${t.instruction || ''}|${[...(t.mandatoryClasses || [])].sort().join(',')}|${[...(t.ignoredClasses || [])].sort().join(',')}|${t.class || ''}`;
let _uidSeq = 0;
const newUid = () => `t${Date.now().toString(36)}${(_uidSeq++).toString(36)}`;

export default function AppBuilder() {
  const { id } = useParams();
  const navigate = useNavigate();

  const [app, setApp] = useState(null);
  const [modelAssets, setModelAssets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [buildLoading, setBuildLoading] = useState(false);
  const [showModelModal, setShowModelModal] = useState(false);
  const [showProfileModal, setShowProfileModal] = useState(false);
  const [confirmConfig, setConfirmConfig] = useState(null);
  
  const pollingRef = useRef(null);
  const logEndRef = useRef(null);

  useEffect(() => {
    loadData();
    return () => { if (pollingRef.current) clearInterval(pollingRef.current); };
  }, [id]);

  useEffect(() => {
    if (app?.build_status === 'building') {
      if (!pollingRef.current) pollingRef.current = setInterval(loadData, 2000);
    } else {
      if (pollingRef.current) { clearInterval(pollingRef.current); pollingRef.current = null; }
    }
  }, [app?.build_status]);

  useEffect(() => {
    if (logEndRef.current) logEndRef.current.scrollIntoView({ behavior: 'smooth' });
  }, [app?.build_log]);

  const loadData = async () => {
    try {
      const res = await getApp(id);
      setApp(res.data);
      const mr = await getModels();
      const ids = res.data.model_asset_ids || [];
      const filtered = mr.data.filter(m => ids.includes(m.id));
      setModelAssets(filtered);
      setLoading(false);
    } catch { navigate('/'); }
  };

  const handleStartBuild = async () => {
    setBuildLoading(true);
    try {
      await buildAPK(id);
      await loadData();
    } catch (err) {
      alert(err.response?.data?.detail || "Failed to start build.");
    } finally {
      setBuildLoading(false);
    }
  };

  const handleResetBuild = async () => {
    await updateApp(id, { build_status: 'idle', build_step: '' });
    loadData();
  };

  const handleDownloadAPK = () => {
    // Plain browser download (streams to disk). If the server refuses, the
    // browser's download list shows the failure; the backend log has the reason.
    const a = document.createElement('a');
    a.href = apkUrl(id);
    a.download = `${app.name.toLowerCase().replace(/ /g, '_')}.apk`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  // "Download for PC": tasks + scan logic + the original .pt models, imported by pc_runner.exe
  const handleDownloadPcPack = async () => {
    try {
      const r = await downloadPcPack(id);
      const url = URL.createObjectURL(r.data);
      const a = document.createElement('a');
      a.href = url; a.download = `${app.name.toLowerCase().replace(/ /g, '_')}_pc_pack.zip`;
      a.click(); URL.revokeObjectURL(url);
      const warn = r.headers && r.headers['x-pack-warnings'];
      if (warn) alert(`Downloaded, but: ${warn}`);
    } catch (e) {
      let msg = 'PC pack download failed.';
      try { msg = JSON.parse(await e.response.data.text()).detail || msg; } catch { /* keep default */ }
      alert(msg);
    }
  };

  // Swap in an updated .pt for a model already in this app. Same model id, so the
  // tasks keep their Mandatory/Ignore class rules -- nothing to set up again.
  const [replacingId, setReplacingId] = useState(null);
  const handleReplacePt = async (mid, file) => {
    if (!file) return;
    setReplacingId(mid);
    try {
      await replaceModelPt(mid, file);
      for (;;) {
        await new Promise(r => setTimeout(r, 3000));
        const st = (await getModelStatus(mid)).data;
        if (st.status === 'ready') break;
        if (st.status === 'error') throw new Error(st.error_message || 'Conversion failed');
      }
      await loadData();
      alert('Model replaced. Your class rules were kept. Rebuild the APK to bundle the new model.');
    } catch (e) {
      alert(e?.response?.data?.detail || e.message || 'Replace failed.');
    } finally {
      setReplacingId(null);
    }
  };

  const removeModel = async (mid) => {
    const newIds = app.model_asset_ids.filter(i => i !== mid);
    await updateApp(id, { model_asset_ids: newIds });
    loadData();
  };

  const removeTask = (taskIdx) => {
    setConfirmConfig({
      title: 'Remove Inspection Task',
      message: 'This inspection task will be removed from the app configuration.',
      confirmLabel: 'Remove',
      onConfirm: async () => {
        setConfirmConfig(null);
        const newTasks = app.inspection_tasks.filter((_, i) => i !== taskIdx);
        await updateApp(id, { inspection_tasks: newTasks });
        loadData();
      },
    });
  };

  const [draggedIdx, setDraggedIdx] = useState(null);

  const handleTaskDragOver = (e, toIdx) => {
    e.preventDefault();
    if (draggedIdx === null || draggedIdx === toIdx) return;
    
    const newTasks = [...app.inspection_tasks];
    const movedItem = newTasks[draggedIdx];
    newTasks.splice(draggedIdx, 1);
    newTasks.splice(toIdx, 0, movedItem);
    
    setApp({ ...app, inspection_tasks: newTasks });
    setDraggedIdx(toIdx);
  };

  const handleTaskDragEnd = async () => {
    if (draggedIdx === null) return;
    setDraggedIdx(null);
    await updateApp(id, { inspection_tasks: app.inspection_tasks });
    loadData();
  };

  const [modalStartAtReview, setModalStartAtReview] = useState(false);

  const openProfileModal = (startAtReview = false) => {
    setModalStartAtReview(startAtReview);
    setShowProfileModal(true);
  };

  if (loading) return <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: C.muted }}>Initializing Build Environment...</div>;

  const isBuilding = app.build_status === 'building';
  const isReady = app.build_status === 'ready';
  // OCR apps don't use the multiclass inspection-task config at all (scan
  // type, detection method, per-class mandatory/ignore/OCR-target rules) --
  // showing that whole UI was confusing and non-collapsible for an app type
  // it doesn't apply to. Show a plain summary of the actual OCR settings
  // instead.
  const isOcrApp = app.app_settings?.app_type === 'ocr';

  const groupedTasks = (app.inspection_tasks || []).reduce((acc, task) => {
    const code = task.vehicleCode || 'Default';
    if (!acc[code]) acc[code] = [];
    acc[code].push(task);
    return acc;
  }, {});

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', height: '100vh', background: 'var(--bg)' }}>
      {/* Top Header */}
      <div style={{ height: 64, background: C.surface, borderBottom: `1px solid ${C.border}`, display: 'flex', alignItems: 'center', padding: '0 24px', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <button 
            onClick={() => navigate('/')} 
            style={{ 
              background: '#dc143c', 
              border: 'none', 
              color: 'white', 
              padding: '6px 16px', 
              borderRadius: 8, 
              cursor: 'pointer', 
              fontSize: 16, 
              fontWeight: 700,
              display: 'flex',
              alignItems: 'center',
              gap: 8
            }}
          >
            <ArrowLeft size={18} /> Dashboard
          </button>
          <div>
            <div style={{ fontWeight: 800, fontSize: 16 }}>{app.name} <span style={{ color: C.muted, fontWeight: 400, fontSize: 12, marginLeft: 8 }}>{app.package_name}</span></div>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 12 }}>
           <button
             onClick={handleDownloadPcPack}
             title="Tasks, scan logic and .pt models for pc_runner.exe"
             style={{
               background: 'transparent',
               color: 'var(--accent)',
               border: '1px solid var(--accent)',
               padding: '10px 20px',
               borderRadius: 8,
               fontWeight: 700,
               cursor: 'pointer',
               display: 'flex',
               alignItems: 'center',
               gap: 8
             }}
           >
             <Download size={18} /> Download for PC
           </button>
           {isReady && (
             <button 
               onClick={handleDownloadAPK} 
               style={{ 
                 background: 'linear-gradient(135deg, var(--accent), var(--accent2))', 
                 color: '#fff', 
                 border: 'none', 
                 padding: '10px 20px', 
                 borderRadius: 8, 
                 fontWeight: 700, 
                 cursor: 'pointer',
                 display: 'flex',
                 alignItems: 'center',
                 gap: 8
               }}
             >
               <Download size={18} /> Download Ready APK
             </button>
           )}
        </div>
      </div>

      <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1fr 450px', overflow: 'hidden' }}>
        
        {/* LEFT: Build Console */}
        <div style={{ display: 'flex', flexDirection: 'column', padding: 24, gap: 16, borderRight: `1px solid ${C.border}` }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h3 style={{ fontSize: 14, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.5px' }}>Terminal Output</h3>
            {isBuilding && <div style={{ color: C.accent, fontSize: 12, fontWeight: 700, animation: 'pulse 1.5s infinite' }}>{app.build_step || 'Processing...'}</div>}
          </div>
          <div style={{ height: 'calc(100vh - 210px)', background: "black", borderRadius: 12, border: `1px solid ${C.border}`, padding: 20, fontFamily: '"JetBrains Mono", monospace', fontSize: 12, color: 'var(--text)', overflowY: 'auto' }}>
            <pre style={{ margin: 0, whiteSpace: 'pre-wrap' ,color:"white"}}>{app.build_log || 'Build logs will appear here...'}</pre>
            <div ref={logEndRef} />
          </div>
        </div>

        {/* RIGHT SIDEBAR */}
        <div style={{ display: 'flex', flexDirection: 'column', background: C.surface, padding: '24px 20px', gap: 24, overflowY: 'auto' }}>
          
          {/* 1. Grouped Review Table (Top) -- OCR apps get a plain settings
              summary instead: they don't use per-class inspection tasks,
              scan type, or detection method at all. */}
          {isOcrApp ? (
            <div style={{ background: C.surface2, borderRadius: 16, border: `1px solid ${C.border}`, padding: 20 }}>
              <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, textTransform: 'uppercase', marginBottom: 14 }}>OCR Configuration</div>
              {(() => {
                const s = app.app_settings || {};
                const truthSource = s.ocr_truth_source || (s.ocr_use_qr_truth ? 'qr' : 'none');
                const truthLabel = { none: 'None (OCR only, no pass/fail)', qr: 'QR / barcode scan', type: 'Typed fixed value' }[truthSource];
                const truthScanTypeLabel = { engine: 'Engine number sticker', model: 'VIN plate scan', chakan: 'Chakan Plant sticker' }[s.ocr_truth_scan_type || 'engine'];
                const row = (label, value) => (
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '6px 0', borderBottom: `1px solid ${C.border}` }}>
                    <span style={{ color: C.muted }}>{label}</span>
                    <span style={{ fontWeight: 700, textAlign: 'right' }}>{value}</span>
                  </div>
                );
                return (
                  <div>
                    {row('Recognizer engine', (s.ocr_engine || 'crnn').toUpperCase())}
                    {row('Plate / region class', s.ocr_region_class || '(auto)')}
                    {row('Expected text source', truthLabel)}
                    {truthSource === 'qr' && row('Truth value scan format', truthScanTypeLabel)}
                    {truthSource === 'qr' && s.ocr_truth_scan_type === 'engine' && row('Expected code length', s.ocr_expected_length || '(not set)')}
                    {truthSource === 'type' && row('Expected value', s.ocr_truth_text || '(not set)')}
                    {truthSource !== 'none' && row('ML Kit fallback', s.ocr_use_mlkit_fallback ? 'Enabled' : 'Disabled')}
                  </div>
                );
              })()}
              <div style={{ marginTop: 14, fontSize: 11, color: C.muted, lineHeight: 1.5 }}>
                These settings are fixed at creation and aren't editable here yet --
                to change them, create a fresh app from New App with the new values.
              </div>
            </div>
          ) : (
          <div style={{ background: C.surface2, borderRadius: 16, border: `1px solid ${C.border}`, overflow: 'hidden' }}>
            <div style={{ padding: '16px 20px', borderBottom: `1px solid ${C.border}`, display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'rgba(255,255,255,0.02)' }}>
              <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, textTransform: 'uppercase' }}>Inspection Configuration</div>
              <button onClick={() => openProfileModal(true)} style={{ background: 'transparent', border: 'none', color: C.accent, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 700 }}>
                <Edit3 size={14} /> Edit All
              </button>
            </div>

            <div style={{ maxHeight: 400, overflowY: 'auto' }}>
              {Object.keys(groupedTasks).length > 0 ? (
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                  <thead>
                    <tr style={{ textAlign: 'left', borderBottom: `1px solid ${C.border}`, background: 'rgba(0,0,0,0.2)' }}>
                      <th style={{ padding: '10px 16px', color: C.muted, fontWeight: 700 }}>MODEL CODE</th>
                      <th style={{ padding: '10px 16px', color: C.muted, fontWeight: 700 }}>AI TASKS</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(groupedTasks).map(([code, tasks]) => (
                      <tr key={code} style={{ borderBottom: `1px solid ${C.border}` }}>
                        <td style={{ padding: '12px 16px', verticalAlign: 'top' }}>
                          <span style={{ fontWeight: 800, color: C.accent }}>{code}</span>
                        </td>
                        <td style={{ padding: '12px 16px' }}>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                            {tasks.map((t, i) => (
                              <div key={i} style={{ fontSize: 11, display: 'flex', alignItems: 'center', gap: 8 }}>
                                <div style={{ flex: 1 }}>
                                  <div style={{ fontWeight: 700 }}>{i+1}. {t.taskName}</div>
                                  <div style={{ color: C.muted, fontSize: 10 }}>{t.modelName} • {t.classes?.[0]}</div>
                                </div>
                                {t.referenceImage && (
                                  <img
                                    src={getReferenceImageUrl(t.referenceImage)}
                                    alt="ref"
                                    style={{ width: 32, height: 32, objectFit: 'cover', borderRadius: 4, border: `1px solid ${C.accent}`, flexShrink: 0 }}
                                  />
                                )}
                              </div>
                            ))}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div style={{ padding: 40, textAlign: 'center', color: C.muted, fontSize: 13 }}>No tasks configured yet.</div>
              )}
            </div>
          </div>
          )}

          {/* 2. Add Component Button */}
          {!isOcrApp && (
            <button onClick={() => openProfileModal(false)} style={{ width: '100%', padding: '16px', borderRadius: 12, border: `1px dashed ${C.accent}`, background: 'rgba(220, 20, 60, 0.05)', color: C.accent, fontWeight: 800, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
              <Plus size={18} /> Add Component Task
            </button>
          )}

          {/* 3. Available AI Assets */}
          <div style={{ background: C.surface2, borderRadius: 16, padding: 20, border: `1px solid ${C.border}` }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
              <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, textTransform: 'uppercase' }}>Active AI Assets</div>
              <button onClick={() => setShowModelModal(true)} style={{ background: 'transparent', border: 'none', color: C.accent, padding: '4px 8px', fontSize: 11, fontWeight: 700, cursor: 'pointer' }}>+ Add More</button>
            </div>
            
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {modelAssets.length > 0 ? modelAssets.map(m => (
                <div key={m.id} style={{ padding: '10px 14px', background: 'var(--surface)', borderRadius: 10, border: `1px solid ${C.border}`, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div style={{ fontSize: 12, fontWeight: 700 }}>{m.vision_project_name}</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <label style={{ color: C.accent, fontSize: 11, fontWeight: 700, cursor: replacingId ? 'not-allowed' : 'pointer' }} title="Upload an updated .pt for this model; its class rules are kept">
                      {replacingId === m.id ? 'Converting...' : 'Replace .pt'}
                      <input type="file" accept=".pt" style={{ display: 'none' }} disabled={!!replacingId}
                        onChange={e => { handleReplacePt(m.id, e.target.files[0]); e.target.value = ''; }} />
                    </label>
                    <button onClick={() => removeModel(m.id)} style={{ background: 'transparent', border: 'none', color: 'var(--error)', cursor: 'pointer', fontSize: 14 }}>×</button>
                  </div>
                </div>
              )) : <div style={{ color: C.muted, fontSize: 11, textAlign: 'center' }}>No models added.</div>}
            </div>
          </div>

          {/* 4. Build Status / Build APK (Bottom) */}
          <div style={{ background: C.surface2, borderRadius: 16, padding: 20, border: `1px solid ${C.border}`}}>
            <button onClick={handleStartBuild} disabled={isBuilding || buildLoading} style={{ width: '100%', padding: '16px', borderRadius: 12, border: 'none', background: isBuilding ? C.border : 'linear-gradient(135deg, var(--accent), var(--accent2))', color: '#fff', fontWeight: 800, cursor: isBuilding ? 'not-allowed' : 'pointer' }}>
              {isBuilding ? 'Compiling APK...' : 'Build Final APK'}
            </button>
            {isBuilding && (
              <button
                onClick={handleResetBuild}
                style={{ width: '100%', marginTop: 8, padding: '10px', borderRadius: 12, border: `1px solid ${C.error}`, background: 'transparent', color: C.error, fontWeight: 700, cursor: 'pointer', fontSize: 13 }}
              >
                Cancel / Reset Build
              </button>
            )}
          </div>

        </div>
      </div>

      {showModelModal && <ModelModal id={id} appIds={app.model_asset_ids} onClose={() => { setShowModelModal(false); loadData(); }} />}
      {showProfileModal && <ProfileModal existingApp={app} startAtReview={modalStartAtReview} onClose={() => { setShowProfileModal(false); loadData(); }} />}
      
      <style>{` @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.5; } 100% { opacity: 1; } } `}</style>

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

import { Plus, Trash2, Edit3, ChevronRight, ArrowLeft, Check, ChevronDown, ArrowUp, ArrowDown, Search, Download, ImageIcon } from 'lucide-react';

function CountryVariantsModal({ row, models, onChange, onClose }) {
  const cc = row.countryCheck;
  const rules = cc?.rules || [];
  const reader = cc?.reader || 'mlkit';
  const modelClasses = models.find(m => m.id === cc?.modelId)?.classes || [];
  const setCC = (p) => onChange({ countryCheck: { ...cc, ...p } });
  const setRule = (i, p) => setCC({ rules: rules.map((r, k) => k === i ? { ...r, ...p } : r) });
  const inp = { padding: '8px 10px', borderRadius: 8, border: '1px solid #8886', background: 'transparent', color: 'inherit', fontSize: 13 };
  const btn = (on, color) => ({ fontSize: 11, fontWeight: 700, padding: '3px 8px', borderRadius: 6, cursor: 'pointer', border: `1px solid ${on ? color : '#8886'}`, background: on ? color : 'transparent', color: on ? '#fff' : 'inherit' });
  const taskLabel = (ai, i) => `${i + 1}. ${ai.instruction || ai.class || 'Task'}`;
  const classesOf = (ai) => models.find(m => m.id === ai.modelId)?.classes || [];

  // overrides of one rule: [{ aiIdx, mandatoryClasses, ignoredClasses }]
  const setOverride = (ri, oi, p) => setRule(ri, { overrides: (rules[ri].overrides || []).map((o, k) => k === oi ? { ...o, ...p } : o) });
  const addOverride = (ri) => {
    const used = (rules[ri].overrides || []).map(o => o.aiIdx);
    const aiIdx = row.selectedAIModels.findIndex((_, i) => !used.includes(i));
    if (aiIdx < 0) return;
    const ai = row.selectedAIModels[aiIdx];
    // start from the task's current rules so only the differences need changing
    setRule(ri, { overrides: [...(rules[ri].overrides || []), { aiIdx, mandatoryClasses: [...(ai.mandatoryClasses || [])], ignoredClasses: [...(ai.ignoredClasses || [])] }] });
  };
  const pickTask = (ri, oi, aiIdx) => {
    const ai = row.selectedAIModels[aiIdx];
    if (!Number.isInteger(aiIdx) || !ai) return;
    // each task can be changed only once per country
    if ((rules[ri].overrides || []).some((o, k) => k !== oi && o.aiIdx === aiIdx)) return;
    setOverride(ri, oi, { aiIdx, mandatoryClasses: [...(ai.mandatoryClasses || [])], ignoredClasses: [...(ai.ignoredClasses || [])] });
  };
  const toggle = (ri, oi, cls, kind) => {
    const o = rules[ri].overrides[oi];
    const m = new Set(o.mandatoryClasses || []); const ig = new Set(o.ignoredClasses || []);
    if (kind === 'm') { if (m.has(cls)) m.delete(cls); else { m.add(cls); ig.delete(cls); } }
    else { if (ig.has(cls)) ig.delete(cls); else { ig.add(cls); m.delete(cls); } }
    setOverride(ri, oi, { mandatoryClasses: [...m], ignoredClasses: [...ig] });
  };

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 2000, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
      <div style={{ background: '#1e1f26', color: '#eee', width: 'min(820px, 94vw)', maxHeight: '90vh', overflowY: 'auto', borderRadius: 14, padding: 22, boxShadow: '0 10px 40px #000a' }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <div style={{ fontWeight: 900, fontSize: 16 }}>Country Variants - {row.model_code}</div>
          <button type="button" onClick={onClose} style={{ ...inp, cursor: 'pointer' }}>Done</button>
        </div>
        <div style={{ fontSize: 12, opacity: 0.75, marginBottom: 14 }}>
          A decision-maker task runs first: the operator photographs the sticker and ML Kit reads the text. If the text contains a country below, the class rules you set for that country replace the normal rules of the tasks you pick. If no country matches, nothing changes.
        </div>
        {!cc ? (
          <button type="button" onClick={() => onChange({ countryCheck: { description: 'Photograph the sticker where the country name is printed', reader: 'mlkit', rules: [{ keyword: '', overrides: [] }] } })}
            style={{ ...inp, cursor: 'pointer', fontWeight: 800, borderColor: 'var(--accent)' }}>+ Add decision-maker task</button>
        ) : (
          <>
            <div style={{ border: '1px solid #8884', borderRadius: 10, padding: 14, marginBottom: 14 }}>
              <div style={{ fontWeight: 800, marginBottom: 8 }}>Decision-maker task (runs first)</div>
              <div style={{ fontSize: 11, opacity: 0.7, marginBottom: 4 }}>Description shown to the operator</div>
              <input style={{ ...inp, width: '100%', boxSizing: 'border-box', marginBottom: 10 }} value={cc.description || ''} onChange={e => setCC({ description: e.target.value })} />
              <div style={{ fontSize: 11, opacity: 0.7, marginBottom: 4 }}>Reader</div>
              <select style={inp} value={reader} onChange={e => setCC({ reader: e.target.value, modelId: '', regionClass: '', rules: rules.map(r => ({ ...r, matchClass: '' })) })}>
                <option value="mlkit">ML Kit - text on the sticker</option>
                <option value="yolo">YOLO model - class detected</option>
                <option value="ocr">OCR - detect a region, then read its text</option>
              </select>
              {reader !== 'mlkit' && (
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 10 }}>
                  <div>
                    <div style={{ fontSize: 11, opacity: 0.7, marginBottom: 4 }}>Detector model</div>
                    <select style={inp} value={cc.modelId || ''} onChange={e => setCC({ modelId: e.target.value, regionClass: '', rules: rules.map(r => ({ ...r, matchClass: '' })) })}>
                      <option value="">Select model...</option>
                      {models.filter(m => m.model_kind !== 'classifier' && m.model_kind !== 'ocr_cnn' && m.model_kind !== 'ocr_crnn').map(m => <option key={m.id} value={m.id}>{m.vision_project_name}</option>)}
                    </select>
                  </div>
                  {reader === 'ocr' && (
                    <>
                      <div>
                        <div style={{ fontSize: 11, opacity: 0.7, marginBottom: 4 }}>Region class to read</div>
                        <select style={inp} value={cc.regionClass || ''} onChange={e => setCC({ regionClass: e.target.value })} disabled={!cc.modelId}>
                          <option value="">Select class...</option>
                          {modelClasses.map(c => <option key={c} value={c}>{c}</option>)}
                        </select>
                      </div>
                      <div>
                        <div style={{ fontSize: 11, opacity: 0.7, marginBottom: 4 }}>Text reader</div>
                        <select style={inp} value={cc.ocrEngine || 'mlkit'} onChange={e => setCC({ ocrEngine: e.target.value })}>
                          <option value="mlkit">ML Kit</option>
                          <option value="crnn">Trained OCR model (attached to the app)</option>
                        </select>
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>
            {rules.map((r, ri) => (
              <div key={ri} style={{ border: '1px solid #8884', borderRadius: 10, padding: 14, marginBottom: 12 }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10 }}>
                  {reader === 'yolo' ? (
                    <>
                      <span style={{ fontWeight: 800, fontSize: 13 }}>If class detected</span>
                      <select style={inp} value={r.matchClass || ''} onChange={e => setRule(ri, { matchClass: e.target.value, keyword: r.keyword || e.target.value })} disabled={!cc.modelId}>
                        <option value="">Select class...</option>
                        {modelClasses.map(c => <option key={c} value={c}>{c}</option>)}
                      </select>
                      <span style={{ fontSize: 12, opacity: 0.8 }}>country name</span>
                      <input style={{ ...inp, flex: 1 }} placeholder="e.g. Indonesia" value={r.keyword || ''} onChange={e => setRule(ri, { keyword: e.target.value })} />
                    </>
                  ) : (
                    <>
                      <span style={{ fontWeight: 800, fontSize: 13 }}>{reader === 'ocr' ? 'If read text contains' : 'If text contains'}</span>
                      <input style={{ ...inp, flex: 1 }} placeholder="e.g. INDONESIA" value={r.keyword || ''} onChange={e => setRule(ri, { keyword: e.target.value })} />
                    </>
                  )}
                  <button type="button" onClick={() => setCC({ rules: rules.filter((_, k) => k !== ri) })} style={{ ...inp, cursor: 'pointer', color: '#e74c3c' }}>Remove country</button>
                </div>
                {(r.overrides || []).map((o, oi) => {
                  const ai = row.selectedAIModels[o.aiIdx];
                  if (!ai) return (
                    <div key={oi} style={{ background: '#0003', borderRadius: 8, padding: 10, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 10 }}>
                      <span style={{ fontSize: 12, color: '#e74c3c' }}>Invalid task entry - remove it and add again</span>
                      <button type="button" onClick={() => setRule(ri, { overrides: r.overrides.filter((_, k) => k !== oi) })} style={{ ...inp, cursor: 'pointer' }}>Remove</button>
                    </div>
                  );
                  const m = new Set(o.mandatoryClasses || []); const ig = new Set(o.ignoredClasses || []);
                  return (
                    <div key={oi} style={{ background: '#0003', borderRadius: 8, padding: 10, marginBottom: 8 }}>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
                        <span style={{ fontSize: 12, opacity: 0.8 }}>Change task</span>
                        <select style={{ ...inp, flex: 1 }} value={o.aiIdx} onChange={e => pickTask(ri, oi, parseInt(e.target.value, 10))}>
                          {row.selectedAIModels.map((a, i) => <option key={i} value={i}>{taskLabel(a, i)}</option>)}
                        </select>
                        <button type="button" onClick={() => setRule(ri, { overrides: r.overrides.filter((_, k) => k !== oi) })} style={{ ...inp, cursor: 'pointer' }}>x</button>
                      </div>
                      <div style={{ fontSize: 10, opacity: 0.6, marginBottom: 6, textTransform: 'uppercase', fontWeight: 700 }}>For this country: Mandatory = must detect - Ignore = no effect - neither = fail if detected</div>
                      {classesOf(ai).map(c => (
                        <div key={c} style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 4 }}>
                          <span style={{ minWidth: 150, fontSize: 12, fontWeight: 600, color: m.has(c) ? 'var(--accent)' : (ig.has(c) ? '#aaa' : '#e74c3c') }}>{c}</span>
                          <button type="button" style={btn(m.has(c), '#2ecc71')} onClick={() => toggle(ri, oi, c, 'm')}>Mandatory</button>
                          <button type="button" style={btn(ig.has(c), '#7f8c8d')} onClick={() => toggle(ri, oi, c, 'i')}>Ignore</button>
                          {!m.has(c) && !ig.has(c) && <span style={{ fontSize: 10, color: '#e74c3c' }}>FAIL if detected</span>}
                        </div>
                      ))}
                    </div>
                  );
                })}
                <button type="button" disabled={(r.overrides || []).length >= row.selectedAIModels.length} onClick={() => addOverride(ri)} style={{ ...inp, cursor: 'pointer' }}>+ Change another task</button>
              </div>
            ))}
            <div style={{ display: 'flex', gap: 8 }}>
              <button type="button" onClick={() => setCC({ rules: [...rules, { keyword: '', overrides: [] }] })} style={{ ...inp, cursor: 'pointer', fontWeight: 800 }}>+ Add another country</button>
              <button type="button" onClick={() => onChange({ countryCheck: null })} style={{ ...inp, cursor: 'pointer', color: '#e74c3c' }}>Remove decision task</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function ProfileModal({ onClose, existingApp, startAtReview = false }) {
  const navigate = useNavigate();
  const appliedRef = useRef(new Set());
  const [newVinPrompt, setNewVinPrompt] = useState(null);
  const [models, setModels] = useState([]);
  const [masterMappings, setMasterMappings] = useState([]);
  const [engineMappings, setEngineMappings] = useState([]);
  const [loading, setLoading] = useState(true);

  const [profileName, setProfileName] = useState(existingApp?.name || '');
  // Combined app: extra standalone scan buttons (VIN/Engine/Chakan/Class
  // Inspection) on the app's home menu, instead of this profile's own Scan
  // Type below being the app's single fixed entry point. Off by default --
  // a plain app just uses Scan Type directly, no menu screen.
  const [isCombinedApp, setIsCombinedApp] = useState(existingApp?.app_settings?.app_type === 'combined');
  const [combinedCapabilities, setCombinedCapabilities] = useState(existingApp?.app_settings?.combined_capabilities || []);
  const toggleCapability = (cap) => {
    setCombinedCapabilities(prev =>
      prev.includes(cap) ? prev.filter(c => c !== cap) : [...prev, cap]
    );
  };
  const [scanType, setScanType] = useState(existingApp?.app_settings?.scan_type || 'model');
  const isEngineLike = scanType === 'engine' || scanType === 'radiator';
  // Detection engine: 'default' (single target class) or 'multiclass'
  // (mandatory-class checklist + per-class OCR verification).
  const [detectionMethod, setDetectionMethod] = useState(existingApp?.app_settings?.detection_method || 'default');
  const [selectedModelCodes, setSelectedModelCodes] = useState([]);
  const [selectedEngineCodes, setSelectedEngineCodes] = useState([]);
  // When on, this profile's tasks apply to any scanned code -- no Model/
  // Engine code selection required, and at runtime the scanned code stops
  // being checked against Master Data/Engine Data (accepted as-is) and
  // stops being used to filter which tasks show (every scan sees the same
  // checklist). Off by default -- masterdata-restricted behavior is
  // unchanged from before this existed.
  const [openScan, setOpenScan] = useState(!!existingApp?.app_settings?.skip_masterdata_validation);
  // A scanned code that matches none of this app's configured task profiles
  // used to always hard-block ("code not present, rebuild app") -- correct
  // when that really is an unknown/typo'd code, but wrong for a variant
  // that legitimately has no inspection task defined for it (e.g. codes 1,
  // 2, 4 have task profiles but 3 is a real, different variant that simply
  // isn't inspected). On, a scan that matches no profile skips straight to
  // an OK submit (no task, no photo) instead of blocking the operator.
  // Independent of Open Scan: Open Scan accepts everything into the SAME
  // checklist; this instead still uses per-code task mapping when it
  // exists, and only auto-passes the codes that were never mapped at all.
  const [autoSubmitUnmapped, setAutoSubmitUnmapped] = useState(!!existingApp?.app_settings?.unmapped_code_auto_submit);
  const [showDropdown, setShowDropdown] = useState(false);
  const [showEngineDropdown, setShowEngineDropdown] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [engineSearchTerm, setEngineSearchTerm] = useState('');
  const modelDropdownRef = useRef(null);
  const engineDropdownRef = useRef(null);

  // Neither dropdown ever closed itself -- picking a code just added a chip
  // and left the list open, with nothing to click to close it again. Being
  // absolutely-positioned, a left-open list then painted straight over
  // whatever came after it in the form (down to the Review Mappings button),
  // since nothing below it knew to make room. Closing on an outside click
  // (and on Escape) gives the list an obvious way to go away.
  useEffect(() => {
    const handleOutsideClick = (e) => {
      if (showDropdown && modelDropdownRef.current && !modelDropdownRef.current.contains(e.target)) {
        setShowDropdown(false);
      }
      if (showEngineDropdown && engineDropdownRef.current && !engineDropdownRef.current.contains(e.target)) {
        setShowEngineDropdown(false);
      }
    };
    const handleEscape = (e) => {
      if (e.key === 'Escape') {
        setShowDropdown(false);
        setShowEngineDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleOutsideClick);
    document.addEventListener('keydown', handleEscape);
    return () => {
      document.removeEventListener('mousedown', handleOutsideClick);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [showDropdown, showEngineDropdown]);

  const [defaultAIConfigs, setDefaultAIConfigs] = useState([{ _uid: newUid(), modelId: '', class: '', mandatoryClasses: [], ignoredClasses: [], classOcrConfig: {}, instruction: '' }]);

  const [isReviewing, setIsReviewing] = useState(startAtReview);
  const [reviewData, setReviewData] = useState([]); 
  const [variantRow, setVariantRow] = useState(null); // index of the review row whose Country Variants popup is open

  useEffect(() => {
    let mounted = true;

    Promise.all([getModels(), getMasterMappings(), getEngineMappings()]).then(([mr, gr, er]) => {
      if (!mounted) return;

      const availableModels = mr.data.filter(m => m.status === 'ready');
      const allMappings = gr.data;
      const allEngineMappings = er.data;

      setModels(availableModels);
      setMasterMappings(allMappings);
      setEngineMappings(allEngineMappings);
      setLoading(false);

      if (existingApp) {
        let restored_uids = [];
        let codes = [];
        if (existingApp.app_settings?.model_codes) {
          codes = existingApp.app_settings.model_codes;
        } else if (existingApp.app_settings?.model_code) {
          codes = [existingApp.app_settings.model_code];
        }
        setSelectedModelCodes(codes);

        if (existingApp.app_settings?.engine_codes) {
          setSelectedEngineCodes(existingApp.app_settings.engine_codes);
        }

        if (existingApp.app_settings?.default_configs) {
          // Ensure both the single-class ('class') and multi-class
          // ('mandatoryClasses'/'classOcrConfig') fields exist on every config.
          // Note: mandatoryClasses must NOT be back-filled from the legacy
          // single 'class' field — that would silently pre-check a class the
          // user never selected in the Multi-Class checklist.
          const restored = existingApp.app_settings.default_configs.map(c => ({
            ...c,
            _uid: c._uid || newUid(),
            class: c.class || '',
            mandatoryClasses: c.mandatoryClasses || [],
            ignoredClasses: c.ignoredClasses || [],
            classOcrConfig: c.classOcrConfig || {},
          }));
          restored_uids = restored.map(c => c._uid);
          setDefaultAIConfigs(restored);
        }

        // Build the review table from whatever inspection_tasks exist --
        // empty/missing is valid (e.g. a profile whose tasks were only ever
        // added through the quick "Add Component Task" path, which never
        // wrote inspection_tasks on the backend). Gating this whole block on
        // inspection_tasks.length > 0 used to mean reviewData never got set
        // at all in that case, and stayed stuck at its [] initial value for
        // the rest of the modal's life -- Edit All opens straight into
        // isReviewing=true, so that showed as a permanently blank table
        // instead of the codes list it should show (each with no tasks yet).
        const decisionByCode = {};
        const tasksByCode = (existingApp.inspection_tasks || []).reduce((acc, t) => {
          const code = t.vehicleCode || 'Default';
          if (t.decision) { decisionByCode[code] = { ...t.decision }; return acc; }
          if (!acc[code]) acc[code] = [];
          acc[code].push({
            modelId: t.modelId,
            class: t.classes?.[0] || '',
            mandatoryClasses: t.mandatoryClasses || [],
            ignoredClasses: t.ignoredClasses || [],
            classOcrConfig: t.classOcrConfig || {},
            instruction: t.instruction || t.taskName || '',
            referenceImage: t.referenceImage || null,
          });
          return acc;
        }, {});

        // An Open Scan profile saves with no Model/Engine codes selected
        // at all (that's the whole point), so there's nothing in `codes`
        // to map over -- its one task group sits under the 'Default' key
        // instead (see handleEnterReview's openScan branch / vehicleCode:
        // '' in handleSaveProfile). Rebuild that single row directly
        // rather than producing an empty (and therefore blank-looking)
        // review table.
        // The review table must list EVERY code that has tasks in the builder.
        // It used to be built only from the saved code list (and only model
        // codes, never engine codes), so tasks whose code wasn't in that list
        // showed in the builder but were missing here -- and were then wiped
        // when the profile was saved. Add those codes back as selected.
        const isEngineScan = ['engine', 'radiator'].includes(existingApp.app_settings?.scan_type);
        const savedCodes = isEngineScan ? (existingApp.app_settings?.engine_codes || []) : codes;
        const orphanCodes = Object.keys(tasksByCode).filter(c => c !== 'Default' && !savedCodes.includes(c));
        const reviewCodes = [...savedCodes, ...orphanCodes];
        if (orphanCodes.length > 0) {
          if (isEngineScan) setSelectedEngineCodes(reviewCodes);
          else setSelectedModelCodes(reviewCodes);
        }
        const restoredReviewData = existingApp.app_settings?.skip_masterdata_validation
          ? [{
              id: 'open-scan',
              platform_name: 'Any',
              model_code: '',
              description: 'Open scan -- applies to any scanned code',
              selectedAIModels: tasksByCode['Default'] || [],
            }]
          : reviewCodes.map(code => {
              if (isEngineScan) {
                const em = allEngineMappings.find(m => m.part_no === code) || { part_no: code, sheet_name: 'Unknown', description: '' };
                return { id: em.id || Math.random().toString(), platform_name: em.sheet_name, model_code: em.part_no, description: em.description, selectedAIModels: tasksByCode[code] || [] };
              }
              const mapping = allMappings.find(m => m.model_code === code) || { model_code: code, platform_name: 'Unknown', description: '' };
              return {
                id: mapping.id || Math.random().toString(),
                platform_name: mapping.platform_name,
                model_code: mapping.model_code,
                description: mapping.description,
                selectedAIModels: tasksByCode[code] || []
              };
            });

        // Default tasks saved earlier were already applied to every code.
        // Saved default tasks were already applied to every code; remember by uid.
        (existingApp.app_settings?.default_configs || []).forEach((c, i) => restoredReviewData.forEach(r => appliedRef.current.add(`${r.model_code}|${restored_uids[i]}`)));
        restoredReviewData.forEach(r => { r.countryCheck = decisionByCode[r.model_code || 'Default'] || null; });
        setReviewData(restoredReviewData);
        if (startAtReview) setIsReviewing(true);
      }
    }).catch((err) => {
      if (!mounted) return;
      console.error("Restoration error:", err);
      setLoading(false);
    });

    return () => { mounted = false; };
  }, [existingApp?.id, startAtReview]);

  const handleToggleModelCode = (code) => {
    setSelectedModelCodes(prev =>
      prev.includes(code) ? prev.filter(c => c !== code) : [...prev, code]
    );
  };

  const handleToggleEngineCode = (partNo) => {
    setSelectedEngineCodes(prev =>
      prev.includes(partNo) ? prev.filter(c => c !== partNo) : [...prev, partNo]
    );
  };

  const handleAddDefaultAI = () => {
    setDefaultAIConfigs([...defaultAIConfigs, { _uid: newUid(), modelId: '', class: '', mandatoryClasses: [], ignoredClasses: [], classOcrConfig: {}, instruction: '' }]);
  };

  const handleRemoveDefaultAI = (index) => {
    setDefaultAIConfigs(defaultAIConfigs.filter((_, i) => i !== index));
  };

  const handleUpdateDefaultAI = (index, field, value) => {
    const newConfigs = [...defaultAIConfigs];
    newConfigs[index][field] = value;
    if (field === 'modelId') {
      carryOverClassRules(newConfigs[index], models.find(mod => mod.id === value));
      // A different model is a different task: give it a fresh id so Review
      // treats it as new and adds it to the existing rows.
      newConfigs[index]._uid = newUid();
    }
    setDefaultAIConfigs(newConfigs);
  };

  // ── Multi-class config handlers (default AI configs) ──────────────────────
  // Each class cycles through three states: mandatory (pass) -> ignored (neutral)
  // -> neither (fail if detected) -> back to mandatory.
  const handleToggleMandatoryClass = (index, cls) => {
    const newConfigs = [...defaultAIConfigs];
    const current = newConfigs[index].mandatoryClasses || [];
    newConfigs[index].mandatoryClasses = current.includes(cls)
      ? current.filter(c => c !== cls)
      : [...current, cls];
    // A class can't be both mandatory and ignored.
    newConfigs[index].ignoredClasses = (newConfigs[index].ignoredClasses || []).filter(c => c !== cls);
    setDefaultAIConfigs(newConfigs);
  };

  const handleToggleIgnoredClass = (index, cls) => {
    const newConfigs = [...defaultAIConfigs];
    const current = newConfigs[index].ignoredClasses || [];
    newConfigs[index].ignoredClasses = current.includes(cls)
      ? current.filter(c => c !== cls)
      : [...current, cls];
    // A class can't be both mandatory and ignored.
    newConfigs[index].mandatoryClasses = (newConfigs[index].mandatoryClasses || []).filter(c => c !== cls);
    setDefaultAIConfigs(newConfigs);
  };

  // The OCR recognizer actually attached to THIS app -- `models` is the
  // whole platform-wide library (getModels() above isn't scoped to this
  // app), so checking .some(...) on it only proves a recognizer exists
  // *somewhere*, not that this app has one. That mismatch is exactly what
  // let "Trained OCR model" show as selectable with nothing behind it: the
  // dropdown didn't distinguish "no recognizer anywhere" from "a
  // recognizer exists, just not attached here." Scoping to
  // existingApp.model_asset_ids fixes both that and lets the dropdown show
  // which bundle it'll actually use.
  const attachedOcrRecognizer = models.find(m =>
    (m.model_kind === 'ocr_cnn' || m.model_kind === 'ocr_crnn') &&
    (existingApp?.model_asset_ids || []).includes(m.id)
  );

  // Classifier models (model_kind "classifier": a YOLO-cls .pt converted like a
  // detector). Any ready classifier in the library can be picked right on the
  // class row -- it is attached to the app automatically when the profile is
  // saved, so it doesn't have to be added to the app beforehand.
  const classifierModels = models.filter(m => m.model_kind === 'classifier');
  const attachedClassifier = classifierModels.find(m =>
    (existingApp?.model_asset_ids || []).includes(m.id)
  ) || null;
  const classifierFor = (id) =>
    classifierModels.find(m => m.id === id) || attachedClassifier || classifierModels[0] || null;
  const classifierLabelsFor = (id) => {
    const raw = classifierFor(id)?.classes;
    if (Array.isArray(raw)) return raw;
    try { return JSON.parse(raw || '[]'); } catch { return []; }
  };

  // Per-class classifier settings live in the same classOcrConfig entry as the OCR
  // ones (classifyEnabled / classifyPassLabel / classifyMinConf / classifyMargin).
  const patchClassClassify = (index, cls, patch) => {
    const newConfigs = [...defaultAIConfigs];
    const cfg = { ...(newConfigs[index].classOcrConfig || {}) };
    cfg[cls] = { ocrEnabled: false, ocrTargetText: '', ocrEngine: 'mlkit', ...(cfg[cls] || {}), ...patch };
    newConfigs[index].classOcrConfig = cfg;
    setDefaultAIConfigs(newConfigs);
  };
  const patchRowClassClassify = (rowIndex, aiIdx, cls, patch) => {
    const newData = [...reviewData];
    const cfg = { ...(newData[rowIndex].selectedAIModels[aiIdx].classOcrConfig || {}) };
    cfg[cls] = { ocrEnabled: false, ocrTargetText: '', ocrEngine: 'mlkit', ...(cfg[cls] || {}), ...patch };
    newData[rowIndex].selectedAIModels[aiIdx].classOcrConfig = cfg;
    setReviewData(newData);
  };
  const enableClassify = (cfg) => {
    const model = classifierFor(cfg?.classifyModelId);
    const labels = classifierLabelsFor(model?.id);
    return {
      classifyEnabled: !cfg?.classifyEnabled,
      classifyModelId: model?.id || '',
      classifyPassLabel: cfg?.classifyPassLabel || labels[0] || '',
      classifyMinConf: cfg?.classifyMinConf ?? 0.5,
      classifyMargin: cfg?.classifyMargin ?? 0.12,
    };
  };
  // Switching the classifier model also resets the pass label to one it really has.
  const classifierSwitch = (id) => ({
    classifyModelId: id,
    classifyPassLabel: classifierLabelsFor(id)[0] || '',
  });

  const handleToggleClassOcr = (index, cls) => {
    const newConfigs = [...defaultAIConfigs];
    const ocr = { ...(newConfigs[index].classOcrConfig || {}) };
    if (ocr[cls]?.ocrEnabled) {
      ocr[cls] = { ...(ocr[cls] || {}), ocrEnabled: false, ocrTargetText: '', ocrEngine: ocr[cls]?.ocrEngine || 'mlkit' };
    } else {
      ocr[cls] = { ...(ocr[cls] || {}), ocrEnabled: true, ocrTargetText: ocr[cls]?.ocrTargetText || '', ocrEngine: ocr[cls]?.ocrEngine || (attachedOcrRecognizer ? 'crnn' : 'mlkit') };
    }
    newConfigs[index].classOcrConfig = ocr;
    setDefaultAIConfigs(newConfigs);
  };

  const handleClassOcrText = (index, cls, text) => {
    const newConfigs = [...defaultAIConfigs];
    const ocr = { ...(newConfigs[index].classOcrConfig || {}) };
    ocr[cls] = { ...(ocr[cls] || {}), ocrTargetText: text };
    newConfigs[index].classOcrConfig = ocr;
    setDefaultAIConfigs(newConfigs);
  };

  const handleClassOcrEngine = (index, cls, engine) => {
    const newConfigs = [...defaultAIConfigs];
    const ocr = { ...(newConfigs[index].classOcrConfig || {}) };
    ocr[cls] = { ...(ocr[cls] || {}), ocrEngine: engine };
    newConfigs[index].classOcrConfig = ocr;
    setDefaultAIConfigs(newConfigs);
  };

  const handleClassOcrTruthSource = (index, cls, source) => {
    const newConfigs = [...defaultAIConfigs];
    const ocr = { ...(newConfigs[index].classOcrConfig || {}) };
    ocr[cls] = { ...(ocr[cls] || {}), ocrTruthSource: source };
    newConfigs[index].classOcrConfig = ocr;
    setDefaultAIConfigs(newConfigs);
  };

  // The "default configs" form (defaultAIConfigs/validAI) is only meant to
  // seed a code/VIN that's being added for the first time -- it must not
  // touch rows that already have their own saved task list, or re-entering
  // Review after adding a new VIN re-appends that new task onto every
  // previously configured VIN/model code as well. So: brand-new rows get
  // validAI, rows that already exist keep exactly what they had.
  // Default tasks added after a row already existed (e.g. while updating a
  // profile) are appended to it once; appliedRef remembers what was already
  // applied per code so a task the user deleted from a row doesn't come back.
  const mergeAIModels = (existing, validAI, code, assign) => {
    const fresh = (v) => ({ ...v, _uid: undefined, mandatoryClasses: [...(v.mandatoryClasses || [])], ignoredClasses: [...(v.ignoredClasses || [])], classOcrConfig: { ...(v.classOcrConfig || {}) } });
    if (assign) {
      // The user chose exactly which tasks each code gets (task x VIN grid).
      validAI.forEach(v => appliedRef.current.add(`${code}|${v._uid}`));
      const chosen = (assign[code] || []).map(fresh);
      return existing === undefined ? chosen : [...existing, ...chosen];
    }
    if (existing === undefined) {
      validAI.forEach(v => appliedRef.current.add(`${code}|${v._uid}`));
      return validAI.map(fresh);
    }
    const toAdd = validAI.filter(v => !appliedRef.current.has(`${code}|${v._uid}`));
    toAdd.forEach(v => appliedRef.current.add(`${code}|${v._uid}`));
    return [...existing, ...toAdd.map(fresh)];
  };

  const handleEnterReview = (assign) => {
    const validAI = defaultAIConfigs.filter(c => c.modelId && (detectionMethod === 'multiclass' ? (c.mandatoryClasses?.length > 0 || c.ignoredClasses?.length > 0) : c.class));
    const existingByCode = reviewData.reduce((acc, row) => {
      acc[row.model_code] = row.selectedAIModels;
      return acc;
    }, {});

    // Updating a profile with new codes and/or new tasks: let the user pick,
    // in a task x code grid, exactly which code gets which task.
    if (assign === undefined && !openScan && reviewData.length > 0) {
      const targets = isEngineLike ? selectedEngineCodes : selectedModelCodes;
      const newCodes = targets.filter(c => existingByCode[c] === undefined);
      const oldCodes = targets.filter(c => existingByCode[c] !== undefined);
      const newTasks = validAI.filter(v => oldCodes.some(c => !appliedRef.current.has(`${c}|${v._uid}`)));
      if (newCodes.length > 0 || newTasks.length > 0) {
        const seen = new Set();
        const rows = [];
        [...newTasks, ...validAI, ...reviewData.flatMap(r => r.selectedAIModels || [])].forEach(t => {
          if (!t.modelId || seen.has(taskSig(t))) return;
          seen.add(taskSig(t));
          rows.push(t);
        });
        const columns = [...newCodes, ...(newTasks.length > 0 ? oldCodes : [])];
        const has = {};
        oldCodes.forEach(c => { has[c] = new Set((existingByCode[c] || []).map(taskSig)); });
        const isNewTask = (t) => newTasks.some(n => taskSig(n) === taskSig(t));
        const checked = {};
        rows.forEach((t, i) => columns.forEach(c => {
          if (has[c] && has[c].has(taskSig(t))) return;
          checked[`${i}|${c}`] = newCodes.includes(c) || isNewTask(t);
        }));
        if (rows.length > 0) {
          setNewVinPrompt({ columns, newCodes, rows, has, checked, existingByCode });
          return;
        }
      }
    }

    if (openScan) {
      // No code restriction -- one universal task group (blank model_code)
      // that applies to every scan, instead of one row per selected code.
      setReviewData([{
        id: 'open-scan',
        platform_name: 'Any',
        model_code: '',
        description: 'Open scan -- applies to any scanned code',
        selectedAIModels: mergeAIModels(existingByCode[''], validAI, ''),
      }]);
      setIsReviewing(true);
      return;
    }

    if (isEngineLike) {
      if (selectedEngineCodes.length === 0) {
        alert("Please select at least one Engine Code, or turn on Open Scan.");
        return;
      }
      const newReviewData = selectedEngineCodes.map(partNo => {
        const mapping = engineMappings.find(m => m.part_no === partNo) || { part_no: partNo, sheet_name: 'Unknown', description: '' };
        return {
          id: mapping.id || Math.random().toString(),
          platform_name: mapping.sheet_name,
          model_code: mapping.part_no,
          description: mapping.description,
          selectedAIModels: mergeAIModels(existingByCode[mapping.part_no], validAI, mapping.part_no, assign)
        };
      });
      setReviewData(newReviewData);
    } else {
      if (selectedModelCodes.length === 0) {
        alert("Please select at least one Vehicle Model Code, or turn on Open Scan.");
        return;
      }
      const newReviewData = selectedModelCodes.map(code => {
        const mapping = masterMappings.find(m => m.model_code === code) || { model_code: code, platform_name: 'Unknown', description: '' };
        return {
          id: mapping.id || Math.random().toString(),
          platform_name: mapping.platform_name,
          model_code: mapping.model_code,
          description: mapping.description,
          selectedAIModels: mergeAIModels(existingByCode[code], validAI, code, assign)
        };
      });
      setReviewData(newReviewData);
    }
    setIsReviewing(true);
  };

  const handleUpdateRowAI = (rowIndex, aiIdx, field, value) => {
    const newData = [...reviewData];
    newData[rowIndex].selectedAIModels[aiIdx][field] = value;
    if (field === 'modelId') {
      carryOverClassRules(newData[rowIndex].selectedAIModels[aiIdx], models.find(mod => mod.id === value));
    }
    setReviewData(newData);
  };

  // ── Multi-class config handlers (review rows) ─────────────────────────────
  const handleToggleRowMandatoryClass = (rowIndex, aiIdx, cls) => {
    const newData = [...reviewData];
    const ai = newData[rowIndex].selectedAIModels[aiIdx];
    const current = ai.mandatoryClasses || [];
    ai.mandatoryClasses = current.includes(cls)
      ? current.filter(c => c !== cls)
      : [...current, cls];
    ai.ignoredClasses = (ai.ignoredClasses || []).filter(c => c !== cls);
    setReviewData(newData);
  };

  const handleToggleRowIgnoredClass = (rowIndex, aiIdx, cls) => {
    const newData = [...reviewData];
    const ai = newData[rowIndex].selectedAIModels[aiIdx];
    const current = ai.ignoredClasses || [];
    ai.ignoredClasses = current.includes(cls)
      ? current.filter(c => c !== cls)
      : [...current, cls];
    ai.mandatoryClasses = (ai.mandatoryClasses || []).filter(c => c !== cls);
    setReviewData(newData);
  };

  const handleToggleRowClassOcr = (rowIndex, aiIdx, cls) => {
    const newData = [...reviewData];
    const ocr = { ...(newData[rowIndex].selectedAIModels[aiIdx].classOcrConfig || {}) };
    if (ocr[cls]?.ocrEnabled) {
      ocr[cls] = { ...(ocr[cls] || {}), ocrEnabled: false, ocrTargetText: '', ocrEngine: ocr[cls]?.ocrEngine || 'mlkit' };
    } else {
      ocr[cls] = { ...(ocr[cls] || {}), ocrEnabled: true, ocrTargetText: ocr[cls]?.ocrTargetText || '', ocrEngine: ocr[cls]?.ocrEngine || (attachedOcrRecognizer ? 'crnn' : 'mlkit') };
    }
    newData[rowIndex].selectedAIModels[aiIdx].classOcrConfig = ocr;
    setReviewData(newData);
  };

  const handleRowClassOcrText = (rowIndex, aiIdx, cls, text) => {
    const newData = [...reviewData];
    const ocr = { ...(newData[rowIndex].selectedAIModels[aiIdx].classOcrConfig || {}) };
    ocr[cls] = { ...(ocr[cls] || {}), ocrTargetText: text };
    newData[rowIndex].selectedAIModels[aiIdx].classOcrConfig = ocr;
    setReviewData(newData);
  };

  const handleRowClassOcrEngine = (rowIndex, aiIdx, cls, engine) => {
    const newData = [...reviewData];
    const ocr = { ...(newData[rowIndex].selectedAIModels[aiIdx].classOcrConfig || {}) };
    ocr[cls] = { ...(ocr[cls] || {}), ocrEngine: engine };
    newData[rowIndex].selectedAIModels[aiIdx].classOcrConfig = ocr;
    setReviewData(newData);
  };

  const handleRowClassOcrTruthSource = (rowIndex, aiIdx, cls, source) => {
    const newData = [...reviewData];
    const ocr = { ...(newData[rowIndex].selectedAIModels[aiIdx].classOcrConfig || {}) };
    ocr[cls] = { ...(ocr[cls] || {}), ocrTruthSource: source };
    newData[rowIndex].selectedAIModels[aiIdx].classOcrConfig = ocr;
    setReviewData(newData);
  };

  const handleMoveRowAI = (rowIndex, aiIdx, direction) => {
    const newData = [...reviewData];
    const aiTasks = [...newData[rowIndex].selectedAIModels];
    const targetIdx = direction === 'up' ? aiIdx - 1 : aiIdx + 1;

    if (targetIdx < 0 || targetIdx >= aiTasks.length) return;

    const [movedTask] = aiTasks.splice(aiIdx, 1);
    aiTasks.splice(targetIdx, 0, movedTask);

    newData[rowIndex].selectedAIModels = aiTasks;
    setReviewData(newData);
  };

  const handleAddRowAI = (rowIndex) => {
    const newData = [...reviewData];
    newData[rowIndex].selectedAIModels.push({ modelId: '', class: '', mandatoryClasses: [], ignoredClasses: [], classOcrConfig: {}, instruction: '', referenceImage: null });
    setReviewData(newData);
  };

  const handleRemoveRowAI = (rowIndex, aiIdx) => {
    const newData = [...reviewData];
    newData[rowIndex].selectedAIModels = newData[rowIndex].selectedAIModels.filter((_, i) => i !== aiIdx);
    setReviewData(newData);
  };

  const handleRemoveRow = (rowIndex) => {
    setReviewData(reviewData.filter((_, i) => i !== rowIndex));
  };

  const handleSaveProfile = async () => {
    let finalTasks = [];
    reviewData.forEach(row => {
      // Country Variants: a decision-maker task runs first and picks the branch.
      if (row.countryCheck && (row.countryCheck.rules || []).some(r => (r.keyword || r.matchClass || '').trim())) {
        finalTasks.push({
          modelId: null,
          taskName: row.countryCheck.description || 'Country check',
          modelName: 'ML Kit text',
          classes: [],
          vehicleCode: row.model_code,
          instruction: row.countryCheck.description || '',
          decision: {
            description: row.countryCheck.description || '',
            reader: row.countryCheck.reader || 'mlkit',
            modelId: row.countryCheck.reader && row.countryCheck.reader !== 'mlkit' ? (row.countryCheck.modelId || null) : null,
            regionClass: row.countryCheck.reader === 'ocr' ? (row.countryCheck.regionClass || '') : '',
            ocrEngine: row.countryCheck.reader === 'ocr' ? (row.countryCheck.ocrEngine || 'mlkit') : '',
            rules: (row.countryCheck.rules || [])
              .filter(r => (r.keyword || r.matchClass || '').trim())
              .map(r => ({
                keyword: (r.keyword || r.matchClass).trim(),
                matchClass: row.countryCheck.reader === 'yolo' ? (r.matchClass || '') : '',
                overrides: (r.overrides || []).map(o => {
                  const ai = row.selectedAIModels[o.aiIdx];
                  const mdl = ai && models.find(m => m.id === ai.modelId);
                  if (!mdl) return null;
                  return {
                    taskIndex: o.aiIdx,
                    taskName: ai.instruction || `${row.model_code} - ${mdl.vision_project_name}`,
                    mandatoryClasses: o.mandatoryClasses || [],
                    ignoredClasses: o.ignoredClasses || [],
                  };
                }).filter(Boolean),
              })),
          },
        });
      }
      row.selectedAIModels.forEach(ai => {
        const model = models.find(m => m.id === ai.modelId);
        if (model) {
          const isMulti = detectionMethod === 'multiclass';
          finalTasks.push({
            modelId: ai.modelId,
            taskName: ai.instruction || `${row.model_code} - ${model.vision_project_name}`,
            modelName: model.vision_project_name,
            // Multi-class detects every trained class and evaluates a checklist;
            // default detects the single chosen target class.
            classes: isMulti ? (model.classes || []) : [ai.class],
            mandatoryClasses: isMulti ? (ai.mandatoryClasses || []) : [],
            ignoredClasses: isMulti ? (ai.ignoredClasses || []) : [],
            classOcrConfig: isMulti ? (ai.classOcrConfig || {}) : {},
            tflitePath: model.tflite_path,
            labelsPath: model.labels_path,
            vehicleCode: row.model_code,
            instruction: ai.instruction,
            referenceImage: ai.referenceImage || null,
          });
        }
      });
    });

    if (finalTasks.length === 0) {
      alert('Please configure at least one AI model for your selections.');
      return;
    }

    try {
      // Union with whatever's already attached to the app (existingApp.model_asset_ids)
      // instead of replacing it outright -- the OCR recognizer (and anything else
      // attached via "Add Model" but not directly used as a task's modelId, since
      // classOcrConfig only references it indirectly via ocrEngine: 'crnn'/'cnn')
      // would otherwise silently fall out of the app's model list on every save,
      // leaving the build with no recognizer to load and everything falling back
      // to ML Kit.
      // Classifiers picked on a class row ("Classify region") are attached too.
      const classifierIds = finalTasks.flatMap(t =>
        Object.values(t.classOcrConfig || {})
          .filter(c => c && c.classifyEnabled && c.classifyModelId)
          .map(c => c.classifyModelId));
      const modelAssetIds = Array.from(new Set([
        ...(existingApp?.model_asset_ids || []),
        ...finalTasks.map(t => t.modelId),
        ...finalTasks.map(t => t.decision?.modelId),
        ...classifierIds,
      ].filter(Boolean)));
      let profileSlug = (profileName || 'app').toLowerCase().replace(/[^a-z0-9]/g, '_');
      if (!profileSlug) profileSlug = 'app';
      // Java package segments can't start with a digit (e.g. "4x4_reverse").
      if (/^[0-9]/.test(profileSlug)) profileSlug = `app_${profileSlug}`;
      const payload = {
        name: profileName || 'New Inspection Profile',
        package_name: existingApp?.package_name || `com.inspection.${profileSlug}`,
        model_asset_ids: modelAssetIds,
        inspection_tasks: finalTasks,
        app_settings: {
          ...(existingApp?.app_settings || {}),
          app_type: isCombinedApp ? 'combined' : 'sequential',
          combined_capabilities: isCombinedApp ? combinedCapabilities : [],
          scan_type: scanType,
          detection_method: detectionMethod,
          skip_masterdata_validation: openScan,
          unmapped_code_auto_submit: autoSubmitUnmapped,
          model_codes: selectedModelCodes,
          model_code: selectedModelCodes[0],
          engine_codes: selectedEngineCodes,
          default_configs: defaultAIConfigs
        }
      };

      if (existingApp) {
        await updateApp(existingApp.id, payload);
        onClose();
      } else {
        const r = await createApp(payload);
        onClose();
        navigate(`/apps/${r.data.id}`);
      }
    } catch { alert('Failed to save profile'); }
  };

  const filteredMappings = masterMappings.filter(m =>
    m.model_code.toLowerCase().includes(searchTerm.toLowerCase()) ||
    m.platform_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
    (m.description || '').toLowerCase().includes(searchTerm.toLowerCase())
  );

  const groupedMappings = filteredMappings.reduce((acc, m) => {
    if (!acc[m.platform_name]) acc[m.platform_name] = [];
    acc[m.platform_name].push(m);
    return acc;
  }, {});

  const filteredEngineMappings = engineMappings.filter(m =>
    m.part_no.toLowerCase().includes(engineSearchTerm.toLowerCase()) ||
    m.sheet_name.toLowerCase().includes(engineSearchTerm.toLowerCase()) ||
    (m.model_name || '').toLowerCase().includes(engineSearchTerm.toLowerCase()) ||
    (m.description || '').toLowerCase().includes(engineSearchTerm.toLowerCase())
  );

  const groupedEngineMappings = filteredEngineMappings.reduce((acc, m) => {
    if (!acc[m.sheet_name]) acc[m.sheet_name] = [];
    acc[m.sheet_name].push(m);
    return acc;
  }, {});


  if (loading) return null;

  const setCells = (fn) => setNewVinPrompt(p => {
    const checked = { ...p.checked };
    Object.keys(checked).forEach(k => { const [i, ...rest] = k.split('|'); checked[k] = fn(Number(i), rest.join('|'), checked[k]); });
    return { ...p, checked };
  });
  const copyFromCode = (col, src) => {
    if (!src) return;
    const srcSigs = new Set((newVinPrompt.existingByCode[src] || []).map(taskSig));
    setCells((i, c, cur) => c === col ? srcSigs.has(taskSig(newVinPrompt.rows[i])) : cur);
  };
  const promptModal = newVinPrompt && (() => {
    const P = newVinPrompt;
    const needle = (P.filter || '').trim().toLowerCase();
    const codes = P.columns.filter(c => !needle || c.toLowerCase().includes(needle));
    const taskTitle = (t) => t.instruction || models.find(x => x.id === t.modelId)?.vision_project_name || t.modelId;
    const taskSub = (t) => {
      const mn = models.find(x => x.id === t.modelId)?.vision_project_name || '';
      const rule = detectionMethod === 'multiclass' ? `${(t.mandatoryClasses || []).length} mandatory, ${(t.ignoredClasses || []).length} ignored` : (t.class || '');
      return [mn, rule].filter(Boolean).join(' · ');
    };
    const cell = { padding: '8px 10px', borderBottom: '1px solid #333' };
    const btn = { padding: '6px 10px', borderRadius: 6, border: '1px solid #555', background: '#222', color: '#fff', fontSize: 12, cursor: 'pointer' };
    return (
      <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.9)', zIndex: 1100, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
        <div style={{ background: '#141414', color: '#fff', border: '1px solid #444', borderRadius: 16, padding: 24, width: '100%', maxWidth: 1000, height: '88vh', display: 'flex', flexDirection: 'column' }}>
          <h3 style={{ fontSize: 20, fontWeight: 800, marginBottom: 6 }}>Choose tasks for each {isEngineLike ? 'engine code' : 'VIN'}</h3>
          <p style={{ fontSize: 13, color: '#bbb', marginBottom: 12, lineHeight: 1.5 }}>
            Each line is one {isEngineLike ? 'engine code' : 'VIN'}. Tick the tasks it should have.
            <b> NEW</b> = just added. <b>✓ already</b> = it already has that task.
            Use <b>Copy tasks from…</b> to copy another VIN's tasks onto a line.
          </p>
          <div style={{ display: 'flex', gap: 8, marginBottom: 10, alignItems: 'center' }}>
            <input value={P.filter || ''} onChange={e => setNewVinPrompt(p => ({ ...p, filter: e.target.value }))} placeholder="Search VIN..." style={{ flex: 1, padding: '8px 12px', borderRadius: 8, border: '1px solid #555', background: '#000', color: '#fff' }} />
            <button style={btn} onClick={() => setCells(() => true)}>Tick all</button>
            <button style={btn} onClick={() => setCells(() => false)}>Clear all</button>
          </div>
          <div style={{ flex: 1, overflow: 'auto', border: '1px solid #444', borderRadius: 10 }}>
            <table style={{ borderCollapse: 'separate', borderSpacing: 0, fontSize: 13, width: '100%' }}>
              <thead>
                <tr>
                  <th style={{ ...cell, position: 'sticky', top: 0, left: 0, zIndex: 3, background: '#1f1f1f', textAlign: 'left', minWidth: 230 }}>{isEngineLike ? 'Engine code' : 'VIN'}</th>
                  {P.rows.map((t, i) => (
                    <th key={i} style={{ ...cell, position: 'sticky', top: 0, zIndex: 2, background: '#1f1f1f', minWidth: 150, textAlign: 'left', verticalAlign: 'top' }}>
                      <div style={{ fontWeight: 800 }}>{taskTitle(t)}</div>
                      <div style={{ fontSize: 11, color: '#aaa', fontWeight: 400 }}>{taskSub(t)}</div>
                      <label style={{ fontSize: 11, color: '#ccc', cursor: 'pointer' }}>
                        <input type="checkbox" onChange={e => { const on = e.target.checked; setCells((ii, cc, cur) => ii === i ? on : cur); }} /> give to all
                      </label>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {codes.map(c => (
                  <tr key={c}>
                    <td style={{ ...cell, position: 'sticky', left: 0, background: '#141414', zIndex: 1 }}>
                      <div style={{ fontWeight: 700 }}>{c} {P.newCodes.includes(c) && <span style={{ background: '#dc143c', borderRadius: 4, padding: '1px 6px', fontSize: 10, marginLeft: 4 }}>NEW</span>}</div>
                      <select value="" onChange={e => copyFromCode(c, e.target.value)} style={{ marginTop: 4, width: '100%', fontSize: 11, background: '#000', color: '#fff', border: '1px solid #555', borderRadius: 6 }}>
                        <option value="">Copy tasks from…</option>
                        {Object.keys(P.existingByCode).filter(o => o !== c).map(o => <option key={o} value={o}>{o}</option>)}
                      </select>
                    </td>
                    {P.rows.map((t, i) => {
                      const k = `${i}|${c}`;
                      return (
                        <td key={i} style={{ ...cell, textAlign: 'center' }}>
                          {k in P.checked
                            ? <input type="checkbox" style={{ width: 18, height: 18 }} checked={P.checked[k]} onChange={() => setNewVinPrompt(p => ({ ...p, checked: { ...p.checked, [k]: !p.checked[k] } }))} />
                            : <span style={{ color: '#7c7' }}>✓ already</span>}
                        </td>
                      );
                    })}
                  </tr>
                ))}
                {codes.length === 0 && <tr><td style={cell} colSpan={P.rows.length + 1}>No match</td></tr>}
              </tbody>
            </table>
          </div>
          <div style={{ display: 'flex', gap: 10, marginTop: 14, justifyContent: 'flex-end' }}>
            <button style={{ ...btn, padding: '10px 16px' }} onClick={() => setNewVinPrompt(null)}>Cancel</button>
            <button style={{ padding: '10px 22px', borderRadius: 8, border: 'none', background: '#dc143c', color: '#fff', fontWeight: 800, cursor: 'pointer' }} onClick={() => {
              const a = {};
              Object.entries(P.checked).forEach(([k, on]) => {
                if (!on) return;
                const [i, ...rest] = k.split('|'); const c = rest.join('|');
                (a[c] = a[c] || []).push(P.rows[Number(i)]);
              });
              setNewVinPrompt(null);
              handleEnterReview(a);
            }}>Continue</button>
          </div>
        </div>
      </div>
    );
  })();

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.85)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
      {promptModal}
      <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 24, padding: 32, width: '100%', maxWidth: isReviewing ? 1150 : 700, maxHeight: '95vh', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 24 }}>

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            {isReviewing && <button onClick={() => setIsReviewing(false)} style={{ background: 'transparent', border: 'none', color: C.muted, cursor: 'pointer', display: 'flex', alignItems: 'center' }}><ArrowLeft size={20} /></button>}
            <h2 style={{ fontSize: 24, fontWeight: 800 }}>{isReviewing ? 'Review & Configure Tasks' : (existingApp ? 'Update Profile' : 'Create Profile')}</h2>
          </div>
          <button onClick={onClose} style={{ background: 'transparent', border: 'none', color: C.muted, fontSize: 32, cursor: 'pointer' }}>×</button>
        </div>

        {!isReviewing ? (
          <>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
              <div>
                <label style={labelStyle}>App Name</label>
                <input style={inputStyle} value={profileName} onChange={e => setProfileName(e.target.value)} placeholder="e.g. Bumper Inspection" />
              </div>

              <div>
                <label style={{ ...labelStyle, display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={isCombinedApp}
                    onChange={e => setIsCombinedApp(e.target.checked)}
                  />
                  Combined app -- extra scan buttons on the home menu
                </label>
                {isCombinedApp && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8, paddingLeft: 4 }}>
                    {[
                      { key: 'vin', label: 'VIN scan', hint: 'Scan a raw VIN barcode, checked against Master Data, then straight into this checklist.' },
                      { key: 'engine', label: 'Engine scan', hint: 'Scan an engine sticker ("PART_NO SERIAL_NO"), checked against Engine Data, then straight into this checklist.' },
                      { key: 'radiator', label: 'Radiator scan', hint: 'Scan a radiator sticker ("MODEL_CODE TRACEABILITY"), model code checked against Engine Data, then straight into this checklist.' },
                      { key: 'chakan', label: 'Chakan Plant scan', hint: 'Scan a Chakan Plant barcode ("VIN_ModelCode_Garbage"), checked against Master Data, then straight into this checklist.' },
                      { key: 'inspection', label: 'Class Inspection', hint: 'Skip the barcode scan entirely -- go straight into this checklist with no vehicle/code attached, for whenever no scan is needed.' },
                    ].map(cap => (
                      <label key={cap.key} style={{ display: 'flex', alignItems: 'flex-start', gap: 6, fontSize: 12, cursor: 'pointer' }}>
                        <input
                          type="checkbox"
                          checked={combinedCapabilities.includes(cap.key)}
                          onChange={() => toggleCapability(cap.key)}
                          style={{ marginTop: 2 }}
                        />
                        <span>
                          <span style={{ fontWeight: 600 }}>{cap.label}</span>
                          <span style={{ display: 'block', color: C.muted, fontSize: 11 }}>{cap.hint}</span>
                        </span>
                      </label>
                    ))}
                  </div>
                )}
                <p style={{ fontSize: 11, color: C.muted, marginTop: 6 }}>
                  Off (default): this app's single entry point uses Scan Type below directly, no
                  menu screen. On: pick which scan buttons show on a home menu instead -- all of
                  them lead into the same checklist configured below.
                </p>
              </div>

              <div>
                <label style={labelStyle}>Scan Type</label>
                <select
                  style={{ ...inputStyle, cursor: 'pointer' }}
                  value={scanType}
                  onChange={e => setScanType(e.target.value)}
                >
                  <option value="model">Model Code (VIN barcode)</option>
                  <option value="engine">Engine Code (Part No + Serial)</option>
                  <option value="radiator">Radiator Scan (Model Code + Radiator Traceability)</option>
                  <option value="chakan">Chakan Plant (VIN_ModelCode_Garbage)</option>
                </select>
              </div>

              <div>
                <label style={labelStyle}>Detection Method</label>
                <select
                  style={{ ...inputStyle, cursor: 'pointer' }}
                  value={detectionMethod}
                  onChange={e => setDetectionMethod(e.target.value)}
                >
                  <option value="default">Default (single target class)</option>
                  <option value="multiclass">Multi-Class (checklist + OCR verify)</option>
                </select>
                <p style={{ fontSize: 11, color: C.muted, marginTop: 6 }}>
                  {detectionMethod === 'multiclass'
                    ? 'Detects all trained classes, checks a mandatory-class list, and can OCR-verify text per class. Chakan & DPI printing still apply.'
                    : 'Detects a single chosen class per task (current behaviour). Chakan & DPI printing still apply.'}
                </p>
              </div>

              <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={openScan}
                  onChange={e => setOpenScan(e.target.checked)}
                  style={{ marginTop: 3 }}
                />
                <span>
                  <span style={{ ...labelStyle, display: 'block', marginBottom: 0 }}>Open Scan (skip masterdata)</span>
                  <span style={{ fontSize: 11, color: C.muted }}>
                    Off (default): pick specific {isEngineLike ? 'Engine' : 'Model'} codes below -- scans
                    are checked against {isEngineLike ? 'Engine Data' : 'Master Data'} and rejected if
                    unknown, and this checklist only shows for those codes. On: no code selection needed --
                    any scanned code is accepted as-is and this same checklist applies to every scan.
                  </span>
                </span>
              </label>

              {openScan ? null : (
                <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={autoSubmitUnmapped}
                    onChange={e => setAutoSubmitUnmapped(e.target.checked)}
                    style={{ marginTop: 3 }}
                  />
                  <span>
                    <span style={{ ...labelStyle, display: 'block', marginBottom: 0 }}>Auto-submit OK for unmapped codes</span>
                    <span style={{ fontSize: 11, color: C.muted }}>
                      Off (default): a scanned code that doesn't match any selected {isEngineLike ? 'Engine' : 'Model'}
                      {' '}code below is rejected ("code not present, rebuild app"). On: instead of rejecting it, the
                      operator sees a "no inspection required for this code" message with a Submit button -- submitting
                      records that VIN with an OK result and no photo, without opening any task. Use this when some
                      variants (like a different trim) legitimately have nothing to inspect, instead of treating them
                      as an error.
                    </span>
                  </span>
                </label>
              )}

              {openScan ? null : !isEngineLike ? (
                <div style={{ position: 'relative' }} ref={modelDropdownRef}>
                  <label style={labelStyle}>Vehicle Model Code (Multi-Select)</label>
                  <div
                    onClick={() => setShowDropdown(!showDropdown)}
                    style={{ ...inputStyle, cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center', minHeight: 46 }}
                  >
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                      {selectedModelCodes.length === 0 ? (
                        <span style={{ color: C.muted }}>Select Model Codes...</span>
                      ) : (
                        selectedModelCodes.map(code => (
                          <span key={code} style={{ background: C.accent, color: '#fff', fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 4, display: 'flex', alignItems: 'center', gap: 4 }}>
                            {code}
                            <span onClick={(e) => { e.stopPropagation(); handleToggleModelCode(code); }} style={{ cursor: 'pointer', opacity: 0.8 }}>×</span>
                          </span>
                        ))
                      )}
                    </div>
                    <ChevronDown size={18} color={C.muted} />
                  </div>
                  {showDropdown && (
                    <div style={{ position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 10, background: 'black', border: `1px solid ${C.border}`, borderRadius: 12, marginTop: 4, maxHeight: 350, overflowY: 'auto', boxShadow: '0 10px 25px rgba(0,0,0,0.5)', display: 'flex', flexDirection: 'column' }}>
                      <div style={{ padding: '12px', borderBottom: `1px solid ${C.border}`, position: 'sticky', top: 0, background: C.surface, zIndex: 5 }}>
                        <div style={{ position: 'relative' }}>
                          <Search size={14} style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: C.muted }} />
                          <input style={{ ...inputStyle, paddingLeft: 36, marginBottom: 0 }} value={searchTerm} onChange={e => setSearchTerm(e.target.value)} placeholder="Search code, platform, description..." onClick={e => e.stopPropagation()} />
                        </div>
                      </div>
                      <div style={{ overflowY: 'auto', flex: 1 }}>
                        {Object.keys(groupedMappings).length === 0 ? (
                          <div style={{ padding: 20, textAlign: 'center', color: C.muted, fontSize: 13 }}>No matches found.</div>
                        ) : (
                          Object.entries(groupedMappings).map(([platform, items]) => (
                            <div key={platform}>
                              <div style={{ padding: '8px 16px', background: C.surface2, fontSize: 10, fontWeight: 800, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.5px', borderBottom: `1px solid ${C.border}` }}>{platform}</div>
                              {items.map(m => {
                                const isSelected = selectedModelCodes.includes(m.model_code);
                                return (
                                  <div key={m.id} onClick={() => handleToggleModelCode(m.model_code)} style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 12, cursor: 'pointer', borderBottom: `1px solid ${C.border}`, background: isSelected ? 'rgba(220, 20, 60, 0.05)' : 'transparent' }}>
                                    <div style={{ width: 18, height: 18, borderRadius: 4, border: `1px solid ${isSelected ? C.accent : C.border}`, background: isSelected ? C.accent : 'transparent', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                                      {isSelected && <Check size={12} color="#fff" />}
                                    </div>
                                    <div style={{ fontSize: 13, fontWeight: 600, color: C.text, lineHeight: '1.4' }}>
                                      <span style={{ fontWeight: 800, color: C.accent }}>{m.model_code}</span>
                                      <span style={{ margin: '0 8px', color: C.muted }}>|</span>
                                      <span>{m.platform_name}</span>
                                      {m.description && (<><span style={{ margin: '0 8px', color: C.muted }}>|</span><span style={{ color: C.muted, fontWeight: 400, fontSize: 12 }}>{m.description}</span></>)}
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          ))
                        )}
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div style={{ position: 'relative' }} ref={engineDropdownRef}>
                  <label style={labelStyle}>Engine Code (Multi-Select)</label>
                  <div
                    onClick={() => setShowEngineDropdown(!showEngineDropdown)}
                    style={{ ...inputStyle, cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center', minHeight: 46 }}
                  >
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                      {selectedEngineCodes.length === 0 ? (
                        <span style={{ color: C.muted }}>Select Engine Codes...</span>
                      ) : (
                        selectedEngineCodes.map(code => (
                          <span key={code} style={{ background: C.accent, color: '#fff', fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 4, display: 'flex', alignItems: 'center', gap: 4 }}>
                            {code}
                            <span onClick={(e) => { e.stopPropagation(); handleToggleEngineCode(code); }} style={{ cursor: 'pointer', opacity: 0.8 }}>×</span>
                          </span>
                        ))
                      )}
                    </div>
                    <ChevronDown size={18} color={C.muted} />
                  </div>
                  {showEngineDropdown && (
                    <div style={{ position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 10, background: 'black', border: `1px solid ${C.border}`, borderRadius: 12, marginTop: 4, maxHeight: 350, overflowY: 'auto', boxShadow: '0 10px 25px rgba(0,0,0,0.5)', display: 'flex', flexDirection: 'column' }}>
                      <div style={{ padding: '12px', borderBottom: `1px solid ${C.border}`, position: 'sticky', top: 0, background: C.surface, zIndex: 5 }}>
                        <div style={{ position: 'relative' }}>
                          <Search size={14} style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: C.muted }} />
                          <input style={{ ...inputStyle, paddingLeft: 36, marginBottom: 0 }} value={engineSearchTerm} onChange={e => setEngineSearchTerm(e.target.value)} placeholder="Search part no, line, model..." onClick={e => e.stopPropagation()} />
                        </div>
                      </div>
                      <div style={{ overflowY: 'auto', flex: 1 }}>
                        {Object.keys(groupedEngineMappings).length === 0 ? (
                          <div style={{ padding: 20, textAlign: 'center', color: C.muted, fontSize: 13 }}>No engine codes found. Add them in Master Data → Engine Codes.</div>
                        ) : (
                          Object.entries(groupedEngineMappings).map(([sheet, items]) => (
                            <div key={sheet}>
                              <div style={{ padding: '8px 16px', background: C.surface2, fontSize: 10, fontWeight: 800, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.5px', borderBottom: `1px solid ${C.border}` }}>{sheet}</div>
                              {items.map(m => {
                                const isSelected = selectedEngineCodes.includes(m.part_no);
                                return (
                                  <div key={m.id} onClick={() => handleToggleEngineCode(m.part_no)} style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 12, cursor: 'pointer', borderBottom: `1px solid ${C.border}`, background: isSelected ? 'rgba(220, 20, 60, 0.05)' : 'transparent' }}>
                                    <div style={{ width: 18, height: 18, borderRadius: 4, border: `1px solid ${isSelected ? C.accent : C.border}`, background: isSelected ? C.accent : 'transparent', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                                      {isSelected && <Check size={12} color="#fff" />}
                                    </div>
                                    <div style={{ fontSize: 13, fontWeight: 600, color: C.text, lineHeight: '1.4' }}>
                                      <span style={{ fontWeight: 800, color: C.accent }}>{m.part_no}</span>
                                      {m.model_name && (<><span style={{ margin: '0 8px', color: C.muted }}>|</span><span>{m.model_name}</span></>)}
                                      {m.description && (<><span style={{ margin: '0 8px', color: C.muted }}>|</span><span style={{ color: C.muted, fontWeight: 400, fontSize: 12 }}>{m.description}</span></>)}
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          ))
                        )}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Default AI Configurations (Step 1) */}
              <div style={{ background: C.surface2, border: `1px solid ${C.border}`, borderRadius: 20, padding: 20 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                  <h3 style={{ fontSize: 14, fontWeight: 800, color: C.text }}>Default AI Configuration (Optional)</h3>
                  <button onClick={handleAddDefaultAI} style={{ background: 'rgba(220, 20, 60, 0.1)', border: `1px solid ${C.accent}`, color: C.accent, padding: '4px 10px', borderRadius: 6, fontSize: 11, fontWeight: 700, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}>
                    <Plus size={14} /> Add Task
                  </button>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                  {defaultAIConfigs.map((config, idx) => (
                    <div key={idx} style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, padding: 16, position: 'relative' }}>
                      {defaultAIConfigs.length > 1 && (
                        <button onClick={() => handleRemoveDefaultAI(idx)} style={{ position: 'absolute', top: 12, right: 12, background: 'transparent', border: 'none', color: C.error, cursor: 'pointer' }}>
                          <Trash2 size={14} />
                        </button>
                      )}
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
                        <div>
                          <label style={labelStyle}>AI Model</label>
                          <SearchableSelect value={config.modelId} placeholder="Select AI Model..."
                            options={models.filter(m => m.model_kind !== 'classifier').map(m => ({ value: m.id, label: m.vision_project_name }))}
                            onChange={(v) => handleUpdateDefaultAI(idx, 'modelId', v)} />
                        </div>
                        <div>
                          {detectionMethod !== 'multiclass' ? (
                            <>
                              <label style={labelStyle}>Detection Class</label>
                              <select style={inputStyle} value={config.class} onChange={(e) => handleUpdateDefaultAI(idx, 'class', e.target.value)} disabled={!config.modelId}>
                                <option value="">Select Class...</option>
                                {models.find(m => m.id === config.modelId)?.classes.map(c => (
                                  <option key={c} value={c}>{c}</option>
                                ))}
                              </select>
                            </>
                          ) : (() => {
                            const modelClasses = models.find(m => m.id === config.modelId)?.classes || [];
                            if (!config.modelId) {
                              return <><label style={labelStyle}>Mandatory Classes</label><span style={{ fontSize: 12, color: C.muted }}>Select a model first</span></>;
                            }
                            return <>
                              <label style={labelStyle}>Class Rules — set each class to Mandatory, Ignore, or leave as Fail-if-detected</label>
                              <div style={{ display: 'flex', flexDirection: 'column', gap: 7, padding: '8px 0' }}>
                                {modelClasses.map(c => {
                                  const isMandatory = (config.mandatoryClasses || []).includes(c);
                                  const isIgnored = (config.ignoredClasses || []).includes(c);
                                  const cn = c.toLowerCase().replace(/_/g, ' ').replace(/-/g, ' ');
                                  const isNotOk = cn.includes('not') && cn.includes('ok');
                                  const ocrCfg = (config.classOcrConfig || {})[c] || {};
                                  return (
                                    <div key={c} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                                        <span style={{ fontSize: 12, fontWeight: 600, color: isMandatory ? (isNotOk ? '#e74c3c' : 'var(--accent)') : (isIgnored ? C.muted : '#e74c3c'), minWidth: 100 }}>{c}</span>
                                        <div style={{ display: 'flex', gap: 4 }}>
                                          <button type="button" onClick={() => handleToggleMandatoryClass(idx, c)}
                                            style={classRuleButtonStyle(isMandatory, isNotOk ? '#e74c3c' : 'var(--accent)')}>
                                            {isNotOk ? 'NOT OK if detected' : 'Mandatory'}
                                          </button>
                                          <button type="button" onClick={() => handleToggleIgnoredClass(idx, c)}
                                            style={classRuleButtonStyle(isIgnored, '#888')}>
                                            Ignore
                                          </button>
                                        </div>
                                        {!isMandatory && !isIgnored && (
                                          <span style={{ fontSize: 10, color: '#e74c3c' }}>FAIL if detected</span>
                                        )}
                                        {isIgnored && (
                                          <span style={{ fontSize: 10, color: C.muted }}>ignored — no effect on result</span>
                                        )}
                                      </div>
                                      {isMandatory && !isNotOk && (
                                        <div style={{ marginLeft: 100, display: 'flex', alignItems: 'center', gap: 8 }}>
                                          <input
                                            type="checkbox"
                                            checked={!!ocrCfg.ocrEnabled}
                                            onChange={() => handleToggleClassOcr(idx, c)}
                                            style={{ width: 13, height: 13, accentColor: '#f0a500', cursor: 'pointer' }}
                                          />
                                          <span style={{ fontSize: 11, color: '#f0a500' }}>Read text</span>
                                          {ocrCfg.ocrEnabled && (
                                            <>
                                              <select
                                                style={{ ...miniSelectStyle, fontSize: 11 }}
                                                value={ocrCfg.ocrEngine || 'mlkit'}
                                                onChange={e => handleClassOcrEngine(idx, c, e.target.value)}
                                              >
                                                <option value="crnn" disabled={!attachedOcrRecognizer}>
                                                  {attachedOcrRecognizer
                                                    ? `Trained OCR model -- ${attachedOcrRecognizer.model_kind === 'ocr_cnn' ? 'CNN' : 'CRNN'} (${attachedOcrRecognizer.vision_project_name})`
                                                    : 'Trained OCR model (attach one first)'}
                                                </option>
                                                <option value="mlkit">Generic (ML Kit)</option>
                                              </select>
                                              <select
                                                style={{ ...miniSelectStyle, fontSize: 11 }}
                                                value={ocrCfg.ocrTruthSource || 'fixed'}
                                                onChange={e => handleClassOcrTruthSource(idx, c, e.target.value)}
                                              >
                                                <option value="fixed">Fixed text</option>
                                                <option value="scan">Scanned {isEngineLike ? 'Engine No' : 'VIN'}</option>
                                              </select>
                                              {(ocrCfg.ocrTruthSource || 'fixed') === 'fixed' && (
                                                <input
                                                  style={{ ...inputStyle, padding: '3px 8px', fontSize: 11, width: 160 }}
                                                  placeholder="Expected text (optional)"
                                                  value={ocrCfg.ocrTargetText || ''}
                                                  onChange={e => handleClassOcrText(idx, c, e.target.value)}
                                                />
                                              )}
                                            </>
                                          )}
                                        </div>
                                      )}
                                      {isMandatory && !isNotOk && (
                                        <div style={{ marginLeft: 100, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                                          <input
                                            type="checkbox"
                                            checked={!!ocrCfg.classifyEnabled}
                                            disabled={classifierModels.length === 0}
                                            onChange={() => patchClassClassify(idx, c, enableClassify(ocrCfg))}
                                            style={{ width: 13, height: 13, accentColor: '#4caf82', cursor: classifierModels.length ? 'pointer' : 'not-allowed' }}
                                          />
                                          <span style={{ fontSize: 11, color: '#4caf82' }}>
                                            Classify region{classifierModels.length === 0 ? ' (upload a classifier model first: New App > Convert New Model > Classifier)' : ''}
                                          </span>
                                          {ocrCfg.classifyEnabled && classifierModels.length > 0 && (
                                            <>
                                              <select
                                                style={{ ...miniSelectStyle, fontSize: 11 }}
                                                value={classifierFor(ocrCfg.classifyModelId)?.id || ''}
                                                onChange={e => patchClassClassify(idx, c, classifierSwitch(e.target.value))}
                                              >
                                                {classifierModels.map(m => <option key={m.id} value={m.id}>{m.vision_project_name}</option>)}
                                              </select>
                                              <span style={{ fontSize: 11, color: C.muted }}>pass if</span>
                                              <select
                                                style={{ ...miniSelectStyle, fontSize: 11 }}
                                                value={ocrCfg.classifyPassLabel || ''}
                                                onChange={e => patchClassClassify(idx, c, { classifyPassLabel: e.target.value })}
                                              >
                                                {classifierLabelsFor(ocrCfg.classifyModelId).map(l => <option key={l} value={l}>{l}</option>)}
                                              </select>
                                              <span style={{ fontSize: 11, color: C.muted }}>min conf</span>
                                              <input type="number" min="0" max="1" step="0.05"
                                                style={{ ...inputStyle, padding: '3px 6px', fontSize: 11, width: 64 }}
                                                value={ocrCfg.classifyMinConf ?? 0.5}
                                                onChange={e => patchClassClassify(idx, c, { classifyMinConf: Math.min(1, Math.max(0, parseFloat(e.target.value) || 0)) })} />
                                              <span style={{ fontSize: 11, color: C.muted }}>crop margin</span>
                                              <input type="number" min="0" max="0.5" step="0.02"
                                                style={{ ...inputStyle, padding: '3px 6px', fontSize: 11, width: 64 }}
                                                value={ocrCfg.classifyMargin ?? 0.12}
                                                onChange={e => patchClassClassify(idx, c, { classifyMargin: Math.min(0.5, Math.max(0, parseFloat(e.target.value) || 0)) })} />
                                            </>
                                          )}
                                        </div>
                                      )}
                                    </div>
                                  );
                                })}
                              </div>
                            </>;
                          })()}
                        </div>
                      </div>
                      <div>
                        <label style={labelStyle}>Instruction / Description</label>
                        <input 
                          style={{ ...inputStyle, marginBottom: 0 }} 
                          value={config.instruction} 
                          onChange={(e) => handleUpdateDefaultAI(idx, 'instruction', e.target.value)} 
                          placeholder="e.g. Please click the rear wheel" 
                        />
                      </div>
                    </div>
                  ))}
                </div>
                <p style={{ fontSize: 11, color: C.muted, marginTop: 12 }}>Tip: These tasks will be applied to every selected vehicle code.</p>
              </div>
            </div>

            <button
              onClick={() => handleEnterReview()}
              disabled={!openScan && (isEngineLike ? selectedEngineCodes.length === 0 : selectedModelCodes.length === 0)}
              style={{ width: '100%', padding: '18px', borderRadius: 14, border: 'none', background: (!openScan && (isEngineLike ? selectedEngineCodes.length === 0 : selectedModelCodes.length === 0)) ? C.border : 'linear-gradient(135deg, var(--accent), var(--accent2))', color: '#fff', fontWeight: 900, fontSize: 16, cursor: 'pointer', marginTop: 10, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}
            >
              REVIEW MAPPINGS <ChevronRight size={20} />
            </button>
          </>
        ) : (
          <>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ textAlign: 'left', borderBottom: `1px solid ${C.border}` }}>
                    <th style={{ ...thStyle, width: '140px' }}>Platform</th>
                    <th style={{ ...thStyle, width: '100px' }}>Code</th>
                    <th style={{ ...thStyle, width: '180px' }}>AI Model</th>
                    <th style={{ ...thStyle, width: '320px' }}>Class</th>
                    <th style={thStyle}>Instruction / Description</th>
                    <th style={{ ...thStyle, width: '80px' }}>Ref. Image</th>
                    <th style={{ ...thStyle, textAlign: 'right', width: '90px' }}>Reorder</th>
                    <th style={{ ...thStyle, textAlign: 'right', width: '50px' }}></th>
                  </tr>
                </thead>
                <tbody>
                  {reviewData.map((row, rowIndex) => (
                    <tr key={row.id} style={{ borderBottom: `1px solid ${C.border}` }}>
                      <td style={tdStyle}><div style={{ fontWeight: 700, fontSize: 13 }}>{row.platform_name}</div></td>
                      <td style={tdStyle}>
                        <span style={{ fontSize: 11, padding: '3px 6px', background: C.surface2, borderRadius: 4, fontWeight: 700, color: C.accent }}>{row.model_code}</span>
                        <div style={{ marginTop: 6 }}>
                          <button type="button" onClick={() => setVariantRow(rowIndex)}
                            style={{ fontSize: 10, fontWeight: 800, padding: '4px 8px', borderRadius: 6, border: `1px solid ${C.accent}`, background: row.countryCheck ? C.accent : 'transparent', color: row.countryCheck ? '#fff' : C.accent, cursor: 'pointer' }}>
                            Country Variants{row.countryCheck ? ` (${(row.countryCheck.rules || []).length})` : ''}
                          </button>
                          {row.countryCheck && (row.countryCheck.rules || []).map((r, k) => (
                            <div key={k} style={{ fontSize: 10, marginTop: 4, color: C.muted, lineHeight: 1.4, maxWidth: 140 }}>
                              <b style={{ color: C.accent }}>{(r.keyword || r.matchClass || '(no text yet)')}</b>
                              {(r.overrides || []).filter(o => Number.isInteger(o.aiIdx) && row.selectedAIModels[o.aiIdx]).length > 0
                                ? ' -> changes ' + (r.overrides || []).filter(o => Number.isInteger(o.aiIdx) && row.selectedAIModels[o.aiIdx]).map(o => `task ${o.aiIdx + 1}`).join(', ')
                                : ' -> no task changed'}
                            </div>
                          ))}
                        </div>
                        {row.description && (
                          <div style={{ fontSize: 11, color: C.muted, marginTop: 5, lineHeight: 1.4, wordBreak: 'break-word', maxWidth: 120 }}>{row.description}</div>
                        )}
                      </td>
                      <td colSpan={6} style={{ padding: 0 }}>
                        <div style={{ display: 'flex', flexDirection: 'column' }}>
                          {row.selectedAIModels.map((ai, aiIdx) => (
                            <div key={aiIdx} style={{ display: 'grid', gridTemplateColumns: '180px 320px 1fr 80px 90px 50px', borderBottom: aiIdx === row.selectedAIModels.length - 1 ? 'none' : `1px solid ${C.border}` }}>
                              <div style={{ padding: '12px 16px' }}>
                                <SearchableSelect style={miniSelectStyle} value={ai.modelId} placeholder="Select Model..."
                                  options={models.filter(m => m.model_kind !== 'classifier').map(m => ({ value: m.id, label: m.vision_project_name }))}
                                  onChange={(v) => handleUpdateRowAI(rowIndex, aiIdx, 'modelId', v)} />
                              </div>
                              <div style={{ padding: '12px 16px' }}>
                                {detectionMethod !== 'multiclass' ? (
                                  <select
                                    style={{ ...miniSelectStyle, width: '100%' }}
                                    value={ai.class}
                                    onChange={(e) => handleUpdateRowAI(rowIndex, aiIdx, 'class', e.target.value)}
                                    disabled={!ai.modelId}
                                  >
                                    {models.find(m => m.id === ai.modelId)?.classes.map(c => (
                                      <option key={c} value={c}>{c}</option>
                                    ))}
                                  </select>
                                ) : (() => {
                                  if (!ai.modelId) return <span style={{ fontSize: 11, color: '#aaa' }}>Select model</span>;
                                  const mc = models.find(m => m.id === ai.modelId)?.classes || [];
                                  return <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                                    <span style={{ fontSize: 10, color: '#aaa', fontWeight: 700, textTransform: 'uppercase', marginBottom: 2 }}>Mandatory = must detect · Ignore = no effect · neither = fail if detected</span>
                                    {mc.map(c => {
                                      const isMandatory = (ai.mandatoryClasses || []).includes(c);
                                      const isIgnored = (ai.ignoredClasses || []).includes(c);
                                      const cn = c.toLowerCase().replace(/_/g, ' ').replace(/-/g, ' ');
                                      const isNotOk = cn.includes('not') && cn.includes('ok');
                                      const ocrCfg = (ai.classOcrConfig || {})[c] || {};
                                      return <div key={c} style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                                          <span style={{ fontSize: 12, fontWeight: 600, color: isMandatory ? (isNotOk ? '#e74c3c' : 'var(--accent)') : (isIgnored ? '#aaa' : '#e74c3c'), minWidth: 70 }}>{c}</span>
                                          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                                            <button type="button" onClick={() => handleToggleRowMandatoryClass(rowIndex, aiIdx, c)}
                                              style={classRuleButtonStyle(isMandatory, isNotOk ? '#e74c3c' : 'var(--accent)')}>
                                              {isNotOk ? 'NOT OK if detected' : 'Mandatory'}
                                            </button>
                                            <button type="button" onClick={() => handleToggleRowIgnoredClass(rowIndex, aiIdx, c)}
                                              style={classRuleButtonStyle(isIgnored, '#888')}>
                                              Ignore
                                            </button>
                                          </div>
                                          {!isMandatory && !isIgnored && (
                                            <span style={{ fontSize: 10, color: '#e74c3c' }}>FAIL if detected</span>
                                          )}
                                          {isIgnored && <span style={{ fontSize: 10, color: '#aaa' }}>ignored</span>}
                                        </div>
                                        {isMandatory && !isNotOk && (
                                          <div style={{ marginLeft: 70, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                                            <input
                                              type="checkbox"
                                              checked={!!ocrCfg.ocrEnabled}
                                              onChange={() => handleToggleRowClassOcr(rowIndex, aiIdx, c)}
                                              style={{ width: 12, height: 12, accentColor: '#f0a500', cursor: 'pointer' }}
                                            />
                                            <span style={{ fontSize: 10, color: '#f0a500' }}>Read text</span>
                                            {ocrCfg.ocrEnabled && (
                                              <>
                                                <select
                                                  style={{ padding: '2px 6px', fontSize: 10, borderRadius: 4, border: '1px solid #444', background: '#111', color: '#fff' }}
                                                  value={ocrCfg.ocrEngine || 'mlkit'}
                                                  onChange={e => handleRowClassOcrEngine(rowIndex, aiIdx, c, e.target.value)}
                                                >
                                                  <option value="crnn" disabled={!attachedOcrRecognizer}>
                                                    {attachedOcrRecognizer
                                                      ? `Trained ${attachedOcrRecognizer.model_kind === 'ocr_cnn' ? 'CNN' : 'CRNN'} (${attachedOcrRecognizer.vision_project_name})`
                                                      : 'Trained model (attach one)'}
                                                  </option>
                                                  <option value="mlkit">ML Kit</option>
                                                </select>
                                                <select
                                                  style={{ padding: '2px 6px', fontSize: 10, borderRadius: 4, border: '1px solid #444', background: '#111', color: '#fff' }}
                                                  value={ocrCfg.ocrTruthSource || 'fixed'}
                                                  onChange={e => handleRowClassOcrTruthSource(rowIndex, aiIdx, c, e.target.value)}
                                                >
                                                  <option value="fixed">Fixed text</option>
                                                  <option value="scan">Scanned {isEngineLike ? 'Engine No' : 'VIN'}</option>
                                                </select>
                                                {(ocrCfg.ocrTruthSource || 'fixed') === 'fixed' && (
                                                  <input
                                                    style={{ padding: '2px 6px', fontSize: 10, width: 110, borderRadius: 4, border: '1px solid #444', background: '#111', color: '#fff' }}
                                                    placeholder="Expected text (optional)"
                                                    value={ocrCfg.ocrTargetText || ''}
                                                    onChange={e => handleRowClassOcrText(rowIndex, aiIdx, c, e.target.value)}
                                                  />
                                                )}
                                              </>
                                            )}
                                          </div>
                                        )}
                                        {isMandatory && !isNotOk && (
                                          <div style={{ marginLeft: 70, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                                            <input
                                              type="checkbox"
                                              checked={!!ocrCfg.classifyEnabled}
                                              disabled={classifierModels.length === 0}
                                              onChange={() => patchRowClassClassify(rowIndex, aiIdx, c, enableClassify(ocrCfg))}
                                              style={{ width: 13, height: 13, accentColor: '#4caf82', cursor: classifierModels.length ? 'pointer' : 'not-allowed' }}
                                            />
                                            <span style={{ fontSize: 11, color: '#4caf82' }}>
                                              Classify region{classifierModels.length === 0 ? ' (upload a classifier model first: New App > Convert New Model > Classifier)' : ''}
                                            </span>
                                            {ocrCfg.classifyEnabled && classifierModels.length > 0 && (
                                              <>
                                                <select
                                                  style={{ ...miniSelectStyle, fontSize: 11 }}
                                                  value={classifierFor(ocrCfg.classifyModelId)?.id || ''}
                                                  onChange={e => patchRowClassClassify(rowIndex, aiIdx, c, classifierSwitch(e.target.value))}
                                                >
                                                  {classifierModels.map(m => <option key={m.id} value={m.id}>{m.vision_project_name}</option>)}
                                                </select>
                                                <span style={{ fontSize: 11, color: C.muted }}>pass if</span>
                                                <select
                                                  style={{ ...miniSelectStyle, fontSize: 11 }}
                                                  value={ocrCfg.classifyPassLabel || ''}
                                                  onChange={e => patchRowClassClassify(rowIndex, aiIdx, c, { classifyPassLabel: e.target.value })}
                                                >
                                                  {classifierLabelsFor(ocrCfg.classifyModelId).map(l => <option key={l} value={l}>{l}</option>)}
                                                </select>
                                                <span style={{ fontSize: 11, color: C.muted }}>min conf</span>
                                                <input type="number" min="0" max="1" step="0.05"
                                                  style={{ ...inputStyle, padding: '3px 6px', fontSize: 11, width: 64 }}
                                                  value={ocrCfg.classifyMinConf ?? 0.5}
                                                  onChange={e => patchRowClassClassify(rowIndex, aiIdx, c, { classifyMinConf: Math.min(1, Math.max(0, parseFloat(e.target.value) || 0)) })} />
                                                <span style={{ fontSize: 11, color: C.muted }}>crop margin</span>
                                                <input type="number" min="0" max="0.5" step="0.02"
                                                  style={{ ...inputStyle, padding: '3px 6px', fontSize: 11, width: 64 }}
                                                  value={ocrCfg.classifyMargin ?? 0.12}
                                                  onChange={e => patchRowClassClassify(rowIndex, aiIdx, c, { classifyMargin: Math.min(0.5, Math.max(0, parseFloat(e.target.value) || 0)) })} />
                                              </>
                                            )}
                                          </div>
                                        )}
                                      </div>;
                                    })}
                                  </div>;
                                })()}
                              </div>
                              <div style={{ padding: '12px 16px' }}>
                                <input
                                  style={{ ...miniSelectStyle, width: '100%', border: 'none', background: 'transparent' }}
                                  value={ai.instruction}
                                  onChange={(e) => handleUpdateRowAI(rowIndex, aiIdx, 'instruction', e.target.value)}
                                  placeholder="e.g. Check front left..."
                                />
                              </div>
                              <div style={{ padding: '8px 12px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                                <label
                                  htmlFor={`ref-img-${rowIndex}-${aiIdx}`}
                                  title={ai.referenceImage ? 'Change reference image' : 'Upload reference image'}
                                  style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                                >
                                  {ai.referenceImage ? (
                                    <img
                                      src={getReferenceImageUrl(ai.referenceImage)}
                                      alt="ref"
                                      style={{ width: 44, height: 44, objectFit: 'cover', borderRadius: 6, border: `2px solid ${C.accent}` }}
                                    />
                                  ) : (
                                    <div style={{ width: 44, height: 44, border: `1px dashed ${C.border}`, borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'center', color: C.muted }}>
                                      <ImageIcon size={18} />
                                    </div>
                                  )}
                                </label>
                                <input
                                  id={`ref-img-${rowIndex}-${aiIdx}`}
                                  type="file"
                                  accept="image/*"
                                  style={{ display: 'none' }}
                                  onChange={async (e) => {
                                    const file = e.target.files?.[0];
                                    if (!file) return;
                                    try {
                                      const r = await uploadReferenceImage(file);
                                      handleUpdateRowAI(rowIndex, aiIdx, 'referenceImage', r.data.filename);
                                    } catch {
                                      alert('Image upload failed. Please try again.');
                                    }
                                    e.target.value = '';
                                  }}
                                />
                              </div>
                              <div style={{ padding: '12px 16px', display: 'flex', gap: 8, justifyContent: 'flex-end', alignItems: 'center' }}>
                                <button 
                                  onClick={() => handleMoveRowAI(rowIndex, aiIdx, 'up')} 
                                  disabled={aiIdx === 0}
                                  style={{ background: 'transparent', border: 'none', color: aiIdx === 0 ? C.border : C.muted, cursor: aiIdx === 0 ? 'default' : 'pointer' }}
                                >
                                  <ArrowUp size={16} />
                                </button>
                                <button 
                                  onClick={() => handleMoveRowAI(rowIndex, aiIdx, 'down')} 
                                  disabled={aiIdx === row.selectedAIModels.length - 1}
                                  style={{ background: 'transparent', border: 'none', color: aiIdx === row.selectedAIModels.length - 1 ? C.border : C.muted, cursor: aiIdx === row.selectedAIModels.length - 1 ? 'default' : 'pointer' }}
                                >
                                  <ArrowDown size={16} />
                                </button>
                              </div>
                              <div style={{ padding: '12px 16px', textAlign: 'right', display: 'flex', gap: 8, justifyContent: 'flex-end', alignItems: 'center' }}>
                                <button onClick={() => handleRemoveRowAI(rowIndex, aiIdx)} style={{ background: 'transparent', border: 'none', color: C.error, cursor: 'pointer' }}>
                                  <Trash2 size={16} />
                                </button>
                              </div>
                            </div>
                          ))}
                          <div style={{ padding: '8px 16px' }}>
                            <button 
                              onClick={() => handleAddRowAI(rowIndex)}
                              style={{ background: 'transparent', border: `1px dashed ${C.accent}`, color: C.accent, padding: '4px 10px', borderRadius: 6, fontSize: 10, fontWeight: 700, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}
                            >
                              <Plus size={12} /> Add AI Task
                            </button>
                          </div>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 10 }}>
              <p style={{ fontSize: 12, color: C.muted }}>Rows: {reviewData.length} | Total Tasks: {reviewData.reduce((acc, curr) => acc + curr.selectedAIModels.length, 0)}</p>
              <button 
                onClick={handleSaveProfile} 
                style={{ padding: '18px 40px', borderRadius: 14, border: 'none', background: 'linear-gradient(135deg, var(--accent), var(--accent2))', color: '#fff', fontWeight: 900, fontSize: 16, cursor: 'pointer' }}
              >
                {existingApp ? 'SAVE ALL CHANGES' : 'GENERATE INSPECTION APP'}
              </button>
            </div>
          </>
        )}
      </div>
      {variantRow !== null && reviewData[variantRow] && (
        <CountryVariantsModal
          row={reviewData[variantRow]}
          models={models}
          onChange={(patch) => { const nd = [...reviewData]; nd[variantRow] = { ...nd[variantRow], ...patch }; setReviewData(nd); }}
          onClose={() => setVariantRow(null)} />
      )}
    </div>
  );
}

const thStyle = { padding: '12px 16px', color: C.muted, fontSize: 11, fontWeight: 700, textTransform: 'uppercase' };
const tdStyle = { padding: '16px', verticalAlign: 'top' };
const miniSelectStyle = { background: C.surface, border: `1px solid ${C.border}`, color: C.text, borderRadius: 6, padding: '4px 8px', fontSize: 12, outline: 'none' };



function ModelModal({ id, appIds, onClose }) {
  const [tab, setTab] = useState('library');
  const [existing, setExisting] = useState([]);
  
  const [ptFile, setPtFile] = useState(null);
  // 'detector' (default, per-class YOLO) or 'detector_char_only' -- the
  // class-agnostic localization-only detector exported from
  // ai-vision-platform's train-seed (class_agnostic=True). Its boxes carry
  // no character identity ("char"/"plate" only), so it's only useful
  // alongside a trained CNN classifier attached as the OCR recognizer.
  const [newModelKind, setNewModelKind] = useState('detector');
  const [modelName, setModelName] = useState('');
  const [classes, setClasses] = useState([]);
  const [analyzing, setAnalyzing] = useState(false);
  const [converting, setConverting] = useState(false);
  const [convLog, setConvLog] = useState('');
  const pollRef = useRef(null);

  // OCR bundle (already-built .tflite + charset, from ai-vision-platform's
  // OCR trainer) -- registered directly via /models/upload-ocr, no .pt
  // conversion needed. Once attached to this app, its class's OCR config
  // can select "Trained OCR model" instead of generic ML Kit.
  const [ocrTfliteFile, setOcrTfliteFile] = useState(null);
  const [ocrCharsetFile, setOcrCharsetFile] = useState(null);
  const [ocrMetaFile, setOcrMetaFile] = useState(null);
  const [ocrModelName, setOcrModelName] = useState('');
  const [ocrModelKind, setOcrModelKind] = useState('ocr_crnn');
  const [ocrUploading, setOcrUploading] = useState(false);
  const [ocrError, setOcrError] = useState('');

  const startOcrUpload = async () => {
    if (!ocrTfliteFile || !ocrCharsetFile || !ocrModelName.trim()) return;
    setOcrUploading(true); setOcrError('');
    try {
      const r = await uploadOcrModel(ocrTfliteFile, ocrCharsetFile, ocrModelName.trim(), ocrModelKind, ocrMetaFile);
      const mid = r.data.id;
      await updateApp(id, { model_asset_ids: [...appIds, mid] });
      onClose();
    } catch (e) {
      setOcrError(e?.response?.data?.detail || 'Upload failed');
      setOcrUploading(false);
    }
  };

  useEffect(() => { getModels().then(r => setExisting(r.data.filter(m => m.status === 'ready'))); }, []);

  const handleDrop = async (file) => {
    setPtFile(file); setModelName(file.name.replace(/\.pt$/i, '').replace(/[_-]/g, ' '));
    setAnalyzing(true);
    try { const r = await extractClasses(file); setClasses(r.data.classes || []); } catch {} finally { setAnalyzing(false); }
  };

  const startConvert = async () => {
    setConverting(true); setConvLog('Initializing conversion...\n');
    try {
      const r = await uploadModel(ptFile, modelName, classes, 640, newModelKind);
      const mid = r.data.id;
      pollRef.current = setInterval(async () => {
        const s = await getModelStatus(mid); setConvLog(s.data.conversion_log || 'Processing...');
        if (s.data.status === 'ready') { 
          clearInterval(pollRef.current);
          await updateApp(id, { model_asset_ids: [...appIds, mid] });
          onClose();
        }
      }, 1500);
    } catch { setConverting(false); }
  };

  const addExisting = async (mid) => {
    if (appIds.includes(mid)) return;
    await updateApp(id, { model_asset_ids: [...appIds, mid] });
    onClose();
  };

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.85)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 24, padding: 32, width: 500, display: 'flex', flexDirection: 'column', gap: 20 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 style={{ fontSize: 20, fontWeight: 800 }}>Add Model to App</h2>
          <button onClick={onClose} style={{ background: 'transparent', border: 'none', color: C.muted, fontSize: 24, cursor: 'pointer' }}>×</button>
        </div>

        <div style={{ display: 'flex', gap: 4, background: C.surface2, padding: 4, borderRadius: 10 }}>
          {['library', 'new', 'ocr'].map(t => <button key={t} onClick={() => setTab(t)} style={{ flex: 1, padding: '8px', borderRadius: 7, border: 'none', background: tab === t ? C.accent : 'transparent', color: tab === t ? '#fff' : C.muted, fontWeight: 700, fontSize: 12, cursor: 'pointer' }}>{t === 'library' ? 'From Library' : t === 'new' ? 'Convert New' : 'OCR Bundle'}</button>)}
        </div>

        {tab === 'ocr' ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <p style={{ fontSize: 12, color: C.muted, margin: 0 }}>
              Attach a trained OCR recognizer (CRNN or per-char CNN) exported from
              ai-vision-platform. Once attached, any class's "Read text" option can
              use it instead of generic ML Kit.
            </p>
            <div>
              <label style={labelStyle}>Model name</label>
              <input style={inputStyle} value={ocrModelName} onChange={e => setOcrModelName(e.target.value)} placeholder="e.g. Plate CRNN v2" />
            </div>
            <div>
              <label style={labelStyle}>Recognizer type</label>
              <select style={inputStyle} value={ocrModelKind} onChange={e => setOcrModelKind(e.target.value)}>
                <option value="ocr_crnn">CRNN (line reader)</option>
                <option value="ocr_cnn">CNN (per-character)</option>
              </select>
            </div>
            <div onClick={() => document.getElementById('ocr-tflite-up').click()} style={{ border: `2px dashed ${C.border}`, borderRadius: 12, padding: 16, textAlign: 'center', cursor: 'pointer', background: C.surface2, fontSize: 12 }}>
              <input id="ocr-tflite-up" type="file" accept=".tflite" style={{ display: 'none' }} onChange={e => setOcrTfliteFile(e.target.files[0])} />
              {ocrTfliteFile ? ocrTfliteFile.name : 'Click to upload recognizer .tflite'}
            </div>
            <div onClick={() => document.getElementById('ocr-charset-up').click()} style={{ border: `2px dashed ${C.border}`, borderRadius: 12, padding: 16, textAlign: 'center', cursor: 'pointer', background: C.surface2, fontSize: 12 }}>
              <input id="ocr-charset-up" type="file" accept=".txt" style={{ display: 'none' }} onChange={e => setOcrCharsetFile(e.target.files[0])} />
              {ocrCharsetFile ? ocrCharsetFile.name : 'Click to upload charset.txt / labels.txt'}
            </div>
            <div onClick={() => document.getElementById('ocr-meta-up').click()} style={{ border: `2px dashed ${C.border}`, borderRadius: 12, padding: 16, textAlign: 'center', cursor: 'pointer', background: C.surface2, fontSize: 12 }}>
              <input id="ocr-meta-up" type="file" accept=".json" style={{ display: 'none' }} onChange={e => setOcrMetaFile(e.target.files[0])} />
              {ocrMetaFile ? ocrMetaFile.name : 'Click to upload meta.json (optional)'}
            </div>
            {ocrError && <span style={{ fontSize: 12, color: '#e74c3c' }}>{ocrError}</span>}
            <button
              disabled={!ocrTfliteFile || !ocrCharsetFile || !ocrModelName.trim() || ocrUploading}
              onClick={startOcrUpload}
              style={{ width: '100%', padding: 14, borderRadius: 10, background: C.accent, color: '#fff', fontWeight: 800, border: 'none', cursor: 'pointer', opacity: (!ocrTfliteFile || !ocrCharsetFile || !ocrModelName.trim() || ocrUploading) ? 0.5 : 1 }}
            >
              {ocrUploading ? 'Uploading...' : 'Upload & Attach'}
            </button>
          </div>
        ) : tab === 'library' ? (
          <div style={{ maxHeight: 300, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 8 }}>
            {existing.map(m => (
              <div key={m.id} onClick={() => addExisting(m.id)} style={{ padding: 12, borderRadius: 10, background: appIds.includes(m.id) ? 'rgba(76,175,130,0.1)' : C.surface2, border: `1px solid ${appIds.includes(m.id) ? C.success : C.border}`, cursor: appIds.includes(m.id) ? 'default' : 'pointer', display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ fontSize: 13, fontWeight: 600 }}>{m.vision_project_name}</span>
                {appIds.includes(m.id) ? <span style={{ color: C.success, fontSize: 11, fontWeight: 700 }}>ADDED</span> : <span style={{ color: C.accent, fontSize: 11, fontWeight: 700 }}>+ ADD</span>}
              </div>
            ))}
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {!converting && (
              <div onClick={() => !analyzing && document.getElementById('file-up').click()} style={{ border: `2px dashed ${C.border}`, borderRadius: 12, padding: 24, textAlign: 'center', cursor: 'pointer', background: C.surface2 }}>
                <input id="file-up" type="file" accept=".pt" style={{ display: 'none' }} onChange={e => handleDrop(e.target.files[0])} />
                {analyzing ? 'Analyzing...' : ptFile ? ptFile.name : 'Click to upload .pt'}
              </div>
            )}
            {ptFile && !converting && (
              <>
                <input style={inputStyle} value={modelName} onChange={e => setModelName(e.target.value)} />
                <div>
                  <label style={labelStyle}>Model kind</label>
                  <select style={inputStyle} value={newModelKind} onChange={e => setNewModelKind(e.target.value)}>
                    <option value="detector">Detector (per-class YOLO)</option>
                    <option value="detector_char_only">Class-agnostic char detector (localization only)</option>
                    <option value="classifier">Classifier (YOLO-cls, e.g. locked / unlocked)</option>
                  </select>
                </div>
                <button onClick={startConvert} style={{ width: '100%', padding: 14, borderRadius: 10, background: C.accent, color: '#fff', fontWeight: 800, border: 'none', cursor: 'pointer' }}>Convert & Add</button>
              </>
            )}
            {converting && <pre style={{ height: 120, background: '#1a1a1a', padding: 12, borderRadius: 10, color: '#fff', fontSize: 10, overflowY: 'auto' }}>{convLog}</pre>}
          </div>
        )}
      </div>
    </div>
  );
}
