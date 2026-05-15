import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { getApp, getModels, buildAPK, downloadAPK, updateApp, uploadModel, getModelStatus, extractClasses, createApp, getMasterMappings, uploadReferenceImage, getReferenceImageUrl } from '../api';

const C = {
  surface: 'var(--surface)', surface2: 'var(--surface2)',
  accent: 'var(--accent)', success: 'var(--success)',
  border: 'var(--border)', text: 'var(--text)', muted: 'var(--text-muted)',
  error: 'var(--error)'
};

const inputStyle = { width: '100%', padding: '10px 14px', borderRadius: 8, border: `1px solid ${C.border}`, background: "black", color: C.text, fontSize: 14 };
const labelStyle = { display: 'block', color: C.muted, fontSize: 11, fontWeight: 700, marginBottom: 6, textTransform: 'uppercase' };

export default function AppBuilder() {
  const { id } = useParams();
  const navigate = useNavigate();

  const [app, setApp] = useState(null);
  const [modelAssets, setModelAssets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [buildLoading, setBuildLoading] = useState(false);
  const [showModelModal, setShowModelModal] = useState(false);
  const [showProfileModal, setShowProfileModal] = useState(false);
  
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

  const handleDownloadAPK = async () => {
    try {
      const r = await downloadAPK(id);
      const url = URL.createObjectURL(r.data);
      const a = document.createElement('a');
      a.href = url; a.download = `${app.name.toLowerCase().replace(/ /g, '_')}.apk`;
      a.click(); URL.revokeObjectURL(url);
    } catch { alert('APK download failed.'); }
  };

  const removeModel = async (mid) => {
    const newIds = app.model_asset_ids.filter(i => i !== mid);
    await updateApp(id, { model_asset_ids: newIds });
    loadData();
  };

  const removeTask = async (taskIdx) => {
    if (!window.confirm('Are you sure you want to remove this inspection task?')) return;
    const newTasks = app.inspection_tasks.filter((_, i) => i !== taskIdx);
    await updateApp(id, { inspection_tasks: newTasks });
    loadData();
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
          
          {/* 1. Grouped Review Table (Top) */}
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
                              <div key={i} style={{ fontSize: 11 }}>
                                <div style={{ fontWeight: 700 }}>{i+1}. {t.taskName}</div>
                                <div style={{ color: C.muted, fontSize: 10 }}>{t.modelName} • {t.classes?.[0]}</div>
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

          {/* 2. Add Component Button */}
          <button onClick={() => openProfileModal(false)} style={{ width: '100%', padding: '16px', borderRadius: 12, border: `1px dashed ${C.accent}`, background: 'rgba(220, 20, 60, 0.05)', color: C.accent, fontWeight: 800, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
            <Plus size={18} /> Add Component Task
          </button>

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
                  <button onClick={() => removeModel(m.id)} style={{ background: 'transparent', border: 'none', color: 'var(--error)', cursor: 'pointer', fontSize: 14 }}>×</button>
                </div>
              )) : <div style={{ color: C.muted, fontSize: 11, textAlign: 'center' }}>No models added.</div>}
            </div>
          </div>

          {/* 4. Build Status / Build APK (Bottom) */}
          <div style={{ background: C.surface2, borderRadius: 16, padding: 20, border: `1px solid ${C.border}`}}>
            <button onClick={handleStartBuild} disabled={isBuilding || buildLoading} style={{ width: '100%', padding: '16px', borderRadius: 12, border: 'none', background: isBuilding ? C.border : 'linear-gradient(135deg, var(--accent), var(--accent2))', color: '#fff', fontWeight: 800, cursor: isBuilding ? 'not-allowed' : 'pointer' }}>
              {isBuilding ? 'Compiling APK...' : 'Build Final APK'}
            </button>
          </div>

        </div>
      </div>

      {showModelModal && <ModelModal id={id} appIds={app.model_asset_ids} onClose={() => { setShowModelModal(false); loadData(); }} />}
      {showProfileModal && <ProfileModal existingApp={app} startAtReview={modalStartAtReview} onClose={() => { setShowProfileModal(false); loadData(); }} />}
      
      <style>{` @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.5; } 100% { opacity: 1; } } `}</style>
    </div>
  );
}

import { Plus, Trash2, Edit3, ChevronRight, ArrowLeft, Check, ChevronDown, ArrowUp, ArrowDown, Search, Download, ImagePlus } from 'lucide-react';

function ProfileModal({ onClose, existingApp, startAtReview = false }) {
  const navigate = useNavigate();
  const [models, setModels] = useState([]);
  const [masterMappings, setMasterMappings] = useState([]);
  const [loading, setLoading] = useState(true);

  // Step 1: Selection State
  const [profileName, setProfileName] = useState(existingApp?.name || '');
  const [selectedModelCodes, setSelectedModelCodes] = useState([]); 
  const [showDropdown, setShowDropdown] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');

  // Step 1: Initial AI Model State (Multiple)
  const [defaultAIConfigs, setDefaultAIConfigs] = useState([{ modelId: '', class: '', instruction: '' }]);

  // Step 2: Review State
  const [isReviewing, setIsReviewing] = useState(startAtReview);
  const [reviewData, setReviewData] = useState([]);
  const [referenceImages, setReferenceImages] = useState({}); // key: "rowIndex_aiIdx" → { url, uploading }

  useEffect(() => {
    Promise.all([getModels(), getMasterMappings()]).then(([mr, gr]) => { 
      const availableModels = mr.data.filter(m => m.status === 'ready');
      const allMappings = gr.data;
      
      setModels(availableModels); 
      setMasterMappings(allMappings);
      setLoading(false); 

      if (existingApp) {
        // 1. Restore selected model codes
        let codes = [];
        if (existingApp.app_settings?.model_codes) {
          codes = existingApp.app_settings.model_codes;
        } else if (existingApp.app_settings?.model_code) {
          codes = [existingApp.app_settings.model_code];
        }
        setSelectedModelCodes(codes);

        // 2. Restore default configs template
        if (existingApp.app_settings?.default_configs) {
          setDefaultAIConfigs(existingApp.app_settings.default_configs);
        }

        // 3. Reconstruct reviewData
        if (existingApp.inspection_tasks && existingApp.inspection_tasks.length > 0) {
          const tasksByCode = existingApp.inspection_tasks.reduce((acc, t) => {
            const code = t.vehicleCode || 'Default';
            if (!acc[code]) acc[code] = [];
            acc[code].push({
              modelId: t.modelId,
              class: t.classes?.[0] || '',
              instruction: t.instruction || t.taskName || ''
            });
            return acc;
          }, {});

          const restoredReviewData = codes.map(code => {
            const mapping = allMappings.find(m => m.model_code === code) || { model_code: code, platform_name: 'Unknown', description: '' };
            return {
              id: mapping.id || Math.random().toString(),
              platform_name: mapping.platform_name,
              model_code: mapping.model_code,
              description: mapping.description,
              selectedAIModels: tasksByCode[code] || []
            };
          });

          setReviewData(restoredReviewData);
          if (startAtReview) setIsReviewing(true);
        }
      }
    }).catch((err) => {
      console.error("Restoration error:", err);
      setLoading(false);
    });
  }, [existingApp, startAtReview]);

  const handleToggleModelCode = (code) => {
    setSelectedModelCodes(prev => 
      prev.includes(code) ? prev.filter(c => c !== code) : [...prev, code]
    );
  };

  const handleAddDefaultAI = () => {
    setDefaultAIConfigs([...defaultAIConfigs, { modelId: '', class: '', instruction: '' }]);
  };

  const handleRemoveDefaultAI = (index) => {
    setDefaultAIConfigs(defaultAIConfigs.filter((_, i) => i !== index));
  };

  const handleUpdateDefaultAI = (index, field, value) => {
    const newConfigs = [...defaultAIConfigs];
    newConfigs[index][field] = value;
    if (field === 'modelId') {
      const m = models.find(mod => mod.id === value);
      newConfigs[index].class = m?.classes[0] || '';
    }
    setDefaultAIConfigs(newConfigs);
  };

  const handleEnterReview = () => {
    if (selectedModelCodes.length === 0) {
      alert("Please select at least one Vehicle Model Code.");
      return;
    }

    // Valid AI configs only
    const validAI = defaultAIConfigs.filter(c => c.modelId && c.class);

    const newReviewData = selectedModelCodes.map(code => {
      const mapping = masterMappings.find(m => m.model_code === code);
      return {
        id: mapping.id,
        platform_name: mapping.platform_name,
        model_code: mapping.model_code,
        description: mapping.description,
        selectedAIModels: validAI.map(v => ({ ...v })) 
      };
    });
    setReviewData(newReviewData);
    setIsReviewing(true);
  };

  const handleUpdateRowAI = (rowIndex, aiIdx, field, value) => {
    const newData = [...reviewData];
    newData[rowIndex].selectedAIModels[aiIdx][field] = value;
    if (field === 'modelId') {
      const m = models.find(mod => mod.id === value);
      newData[rowIndex].selectedAIModels[aiIdx].class = m?.classes[0] || '';
    }
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
    newData[rowIndex].selectedAIModels.push({ modelId: '', class: '', instruction: '' });
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

  const handleReferenceImageUpload = async (rowIndex, aiIdx, file) => {
    if (!file || !existingApp) return;
    const key = `${rowIndex}_${aiIdx}`;
    // Show local preview immediately so user sees selection right away
    const localUrl = URL.createObjectURL(file);
    setReferenceImages(prev => ({ ...prev, [key]: { url: localUrl, uploading: true } }));
    try {
      const taskIndex = reviewData.slice(0, rowIndex).reduce((acc, r) => acc + r.selectedAIModels.length, 0) + aiIdx;
      const fd = new FormData();
      fd.append('file', file);
      await uploadReferenceImage(existingApp.id, taskIndex, fd);
      // Replace local blob URL with server URL after successful upload
      const serverUrl = getReferenceImageUrl(existingApp.id, taskIndex) + '?t=' + Date.now();
      URL.revokeObjectURL(localUrl);
      setReferenceImages(prev => ({ ...prev, [key]: { url: serverUrl, uploading: false } }));
    } catch {
      // Keep the local preview visible even if upload fails
      setReferenceImages(prev => ({ ...prev, [key]: { url: localUrl, uploading: false } }));
      console.error('Reference image upload failed');
    }
  };

  const handleSaveProfile = async () => {
    let finalTasks = [];
    reviewData.forEach(row => {
      row.selectedAIModels.forEach(ai => {
        const model = models.find(m => m.id === ai.modelId);
        if (model) {
          finalTasks.push({
            modelId: ai.modelId,
            taskName: ai.instruction || `${row.model_code} - ${model.vision_project_name}`,
            modelName: model.vision_project_name,
            classes: [ai.class],
            tflitePath: model.tflite_path,
            labelsPath: model.labels_path,
            vehicleCode: row.model_code,
            instruction: ai.instruction
          });
        }
      });
    });

    if (finalTasks.length === 0) {
      alert('Please configure at least one AI model for your selections.');
      return;
    }

    try {
      const modelAssetIds = Array.from(new Set(finalTasks.map(t => t.modelId)));
      const payload = {
        name: profileName || 'New Inspection Profile',
        package_name: existingApp?.package_name || `com.inspection.${(profileName || 'app').toLowerCase().replace(/\s+/g, '_')}`,
        model_asset_ids: modelAssetIds,
        inspection_tasks: finalTasks,
        app_settings: { 
          ...(existingApp?.app_settings || {}), 
          app_type: 'sequential', 
          model_codes: selectedModelCodes,
          model_code: selectedModelCodes[0],
          default_configs: defaultAIConfigs // Persist the template
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

  // Grouping and Filtering logic
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

  if (loading) return null;

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.85)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
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

              <div style={{ position: 'relative' }}>
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
                  <div style={{ position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 10, background:"black", border: `1px solid ${C.border}`, borderRadius: 12, marginTop: 4, maxHeight: 350, overflowY: 'auto', boxShadow: '0 10px 25px rgba(0,0,0,0.5)', display: 'flex', flexDirection: 'column' }}>
                    <div style={{ padding: '12px', borderBottom: `1px solid ${C.border}`, position: 'sticky', top: 0, background: C.surface, zIndex: 5 }}>
                      <div style={{ position: 'relative' }}>
                        <Search size={14} style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: C.muted }} />
                        <input 
                          style={{ ...inputStyle, paddingLeft: 36, marginBottom: 0 }} 
                          value={searchTerm} 
                          onChange={e => setSearchTerm(e.target.value)} 
                          placeholder="Search code, platform, description..."
                          onClick={e => e.stopPropagation()}
                        />
                      </div>
                    </div>
                    
                    <div style={{ overflowY: 'auto', flex: 1 }}>
                      {Object.keys(groupedMappings).length === 0 ? (
                        <div style={{ padding: 20, textAlign: 'center', color: C.muted, fontSize: 13 }}>No matches found.</div>
                      ) : (
                        Object.entries(groupedMappings).map(([platform, items]) => (
                          <div key={platform}>
                            <div style={{ padding: '8px 16px', background: C.surface2, fontSize: 10, fontWeight: 800, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.5px', borderBottom: `1px solid ${C.border}` }}>
                              {platform}
                            </div>
                            {items.map(m => {
                              const isSelected = selectedModelCodes.includes(m.model_code);
                              return (
                                <div 
                                  key={m.id} 
                                  onClick={() => handleToggleModelCode(m.model_code)}
                                  style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 12, cursor: 'pointer', borderBottom: `1px solid ${C.border}`, background: isSelected ? 'rgba(220, 20, 60, 0.05)' : 'transparent' }}
                                >
                                  <div style={{ width: 18, height: 18, borderRadius: 4, border: `1px solid ${isSelected ? C.accent : C.border}`, background: isSelected ? C.accent : 'transparent', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                                    {isSelected && <Check size={12} color="#fff" />}
                                  </div>
                                  <div style={{ fontSize: 13, fontWeight: 600, color: C.text, lineHeight: '1.4' }}>
                                    <span style={{ fontWeight: 800, color: C.accent }}>{m.model_code}</span>
                                    <span style={{ margin: '0 8px', color: C.muted }}>|</span>
                                    <span>{m.platform_name}</span>
                                    {m.description && (
                                      <>
                                        <span style={{ margin: '0 8px', color: C.muted }}>|</span>
                                        <span style={{ color: C.muted, fontWeight: 400, fontSize: 12 }}>{m.description}</span>
                                      </>
                                    )}
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
                          <select style={inputStyle} value={config.modelId} onChange={(e) => handleUpdateDefaultAI(idx, 'modelId', e.target.value)}>
                            <option value="">Select AI Model...</option>
                            {models.map(m => <option key={m.id} value={m.id}>{m.vision_project_name}</option>)}
                          </select>
                        </div>
                        <div>
                          <label style={labelStyle}>Detection Class</label>
                          <select style={inputStyle} value={config.class} onChange={(e) => handleUpdateDefaultAI(idx, 'class', e.target.value)} disabled={!config.modelId}>
                            <option value="">Select Class...</option>
                            {models.find(m => m.id === config.modelId)?.classes.map(c => (
                              <option key={c} value={c}>{c}</option>
                            ))}
                          </select>
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
              onClick={handleEnterReview} 
              disabled={selectedModelCodes.length === 0}
              style={{ width: '100%', padding: '18px', borderRadius: 14, border: 'none', background: selectedModelCodes.length === 0 ? C.border : 'linear-gradient(135deg, var(--accent), var(--accent2))', color: '#fff', fontWeight: 900, fontSize: 16, cursor: 'pointer', marginTop: 10, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}
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
                    <th style={{ ...thStyle, width: '140px' }}>Class</th>
                    <th style={thStyle}>Instruction / Description</th>
                    <th style={{ ...thStyle, width: '90px' }}>Ref. Image</th>
                    <th style={{ ...thStyle, textAlign: 'right', width: '90px' }}>Reorder</th>
                    <th style={{ ...thStyle, textAlign: 'right', width: '50px' }}></th>
                  </tr>
                </thead>
                <tbody>
                  {reviewData.map((row, rowIndex) => (
                    <tr key={row.id} style={{ borderBottom: `1px solid ${C.border}` }}>
                      <td style={tdStyle}><div style={{ fontWeight: 700, fontSize: 13 }}>{row.platform_name}</div></td>
                      <td style={tdStyle}><span style={{ fontSize: 11, padding: '3px 6px', background: C.surface2, borderRadius: 4, fontWeight: 700, color: C.accent }}>{row.model_code}</span></td>
                      <td colSpan={6} style={{ padding: 0 }}>
                        <div style={{ display: 'flex', flexDirection: 'column' }}>
                          {row.selectedAIModels.map((ai, aiIdx) => (
                            <div key={aiIdx} style={{ display: 'grid', gridTemplateColumns: '180px 140px 1fr 90px 90px 50px', borderBottom: aiIdx === row.selectedAIModels.length - 1 ? 'none' : `1px solid ${C.border}` }}>
                              <div style={{ padding: '12px 16px' }}>
                                <select 
                                  style={{ ...miniSelectStyle, width: '100%' }} 
                                  value={ai.modelId} 
                                  onChange={(e) => handleUpdateRowAI(rowIndex, aiIdx, 'modelId', e.target.value)}
                                >
                                  <option value="">Select Model...</option>
                                  {models.map(m => <option key={m.id} value={m.id}>{m.vision_project_name}</option>)}
                                </select>
                              </div>
                              <div style={{ padding: '12px 16px' }}>
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
                              </div>
                              <div style={{ padding: '12px 16px' }}>
                                <input
                                  style={{ ...miniSelectStyle, width: '100%', border: 'none', background: 'transparent' }}
                                  value={ai.instruction}
                                  onChange={(e) => handleUpdateRowAI(rowIndex, aiIdx, 'instruction', e.target.value)}
                                  placeholder="e.g. Check front left..."
                                />
                              </div>
                              <div style={{ padding: '8px 12px', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 4 }}>
                                {(() => {
                                  const key = `${rowIndex}_${aiIdx}`;
                                  const ref = referenceImages[key];
                                  if (ref?.uploading) return <span style={{ fontSize: 10, color: C.muted }}>Uploading…</span>;
                                  if (ref?.url) return (
                                    <div style={{ position: 'relative' }}>
                                      <img src={ref.url} alt="ref" style={{ width: 64, height: 48, objectFit: 'cover', borderRadius: 4, border: `1px solid ${C.border}` }} />
                                      <label style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.4)', borderRadius: 4, cursor: 'pointer', opacity: 0, transition: 'opacity 0.2s' }}
                                        onMouseEnter={e => e.currentTarget.style.opacity = 1}
                                        onMouseLeave={e => e.currentTarget.style.opacity = 0}>
                                        <span style={{ color: '#fff', fontSize: 9, fontWeight: 700 }}>CHANGE</span>
                                        <input type="file" accept="image/*" style={{ display: 'none' }} onChange={e => e.target.files[0] && handleReferenceImageUpload(rowIndex, aiIdx, e.target.files[0])} />
                                      </label>
                                    </div>
                                  );
                                  return (
                                    <label style={{ cursor: existingApp ? 'pointer' : 'not-allowed', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3, padding: '6px 8px', borderRadius: 6, border: `1px dashed ${existingApp ? C.accent : C.border}`, transition: 'background 0.15s' }} title={existingApp ? 'Upload reference image' : 'Save app first'}
                                      onMouseEnter={e => { if (existingApp) e.currentTarget.style.background = 'rgba(255,255,255,0.05)'; }}
                                      onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}>
                                      <ImagePlus size={16} color={existingApp ? C.accent : C.muted} />
                                      <span style={{ fontSize: 9, color: C.muted, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.5 }}>Upload</span>
                                      {existingApp && <input type="file" accept="image/*" style={{ display: 'none' }} onChange={e => e.target.files[0] && handleReferenceImageUpload(rowIndex, aiIdx, e.target.files[0])} />}
                                    </label>
                                  );
                                })()}
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
    </div>
  );
}

const thStyle = { padding: '12px 16px', color: C.muted, fontSize: 11, fontWeight: 700, textTransform: 'uppercase' };
const tdStyle = { padding: '16px', verticalAlign: 'top' };
const miniSelectStyle = { background: C.surface, border: `1px solid ${C.border}`, color: C.text, borderRadius: 6, padding: '4px 8px', fontSize: 12, outline: 'none' };



function ModelModal({ id, appIds, onClose }) {
  const [tab, setTab] = useState('library');
  const [existing, setExisting] = useState([]);
  
  // Conversion state
  const [ptFile, setPtFile] = useState(null);
  const [modelName, setModelName] = useState('');
  const [classes, setClasses] = useState([]);
  const [analyzing, setAnalyzing] = useState(false);
  const [converting, setConverting] = useState(false);
  const [convLog, setConvLog] = useState('');
  const pollRef = useRef(null);

  useEffect(() => { getModels().then(r => setExisting(r.data.filter(m => m.status === 'ready'))); }, []);

  const handleDrop = async (file) => {
    setPtFile(file); setModelName(file.name.replace(/\.pt$/i, '').replace(/[_-]/g, ' '));
    setAnalyzing(true);
    try { const r = await extractClasses(file); setClasses(r.data.classes || []); } catch {} finally { setAnalyzing(false); }
  };

  const startConvert = async () => {
    setConverting(true); setConvLog('Initializing conversion...\n');
    try {
      const r = await uploadModel(ptFile, modelName, classes);
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
          {['library', 'new'].map(t => <button key={t} onClick={() => setTab(t)} style={{ flex: 1, padding: '8px', borderRadius: 7, border: 'none', background: tab === t ? C.accent : 'transparent', color: tab === t ? '#fff' : C.muted, fontWeight: 700, fontSize: 12, cursor: 'pointer' }}>{t === 'library' ? 'From Library' : 'Convert New'}</button>)}
        </div>

        {tab === 'library' ? (
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
