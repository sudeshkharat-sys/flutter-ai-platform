import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { UploadCloud, Check, Box, Zap } from 'lucide-react';
import { uploadModel, getModelStatus, getModels, createApp, extractClasses } from '../api';
import '../styles/NewApp.css';

function DropZone({ onFile, analyzing }) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef();

  const handle = useCallback(
    (f) => { if (f && f.name.endsWith('.pt')) onFile(f); },
    [onFile]
  );

  return (
    <div
      className={`drop-zone${dragging ? ' dragging' : ''}`}
      onDragOver={e => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={e => { e.preventDefault(); setDragging(false); handle(e.dataTransfer.files[0]); }}
      onClick={() => !analyzing && inputRef.current.click()}
      style={{ cursor: analyzing ? 'default' : 'pointer' }}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".pt"
        style={{ display: 'none' }}
        onChange={e => handle(e.target.files[0])}
        disabled={analyzing}
      />
      {analyzing ? (
        <>
          <div className="drop-zone-spinner" />
          <div className="drop-zone-title">Analyzing model...</div>
          <div className="drop-zone-sub">Auto-detecting classes</div>
        </>
      ) : (
        <>
          <div className="drop-zone-upload-icon">
            <UploadCloud size={28} />
          </div>
          <div className="drop-zone-title">Upload YOLO .pt model</div>
          <div className="drop-zone-sub">Drag & drop or click to browse — classes auto-detected</div>
        </>
      )}
    </div>
  );
}

export default function NewApp() {
  const navigate = useNavigate();
  const [step, setStep] = useState(1);
  const [tab, setTab] = useState('upload');

  const [selectedModelIds, setSelectedModelIds] = useState([]);
  const [selectedModelNames, setSelectedModelNames] = useState([]);

  const [ptFile, setPtFile] = useState(null);
  const [modelName, setModelName] = useState('');
  const [classes, setClasses] = useState([]);
  const [analyzing, setAnalyzing] = useState(false);
  const [converting, setConverting] = useState(false);
  const [conversionLog, setConversionLog] = useState('');

  const [existingModels, setExistingModels] = useState([]);
  const [appName, setAppName] = useState('');
  const [packageName, setPackageName] = useState('');

  const pollRef = useRef(null);
  const logEndRef = useRef(null);

  useEffect(() => {
    if (logEndRef.current) logEndRef.current.scrollIntoView({ behavior: 'smooth' });
  }, [conversionLog]);

  useEffect(() => {
    getModels()
      .then(r => setExistingModels(r.data.filter(m => m.status === 'ready')))
      .catch(() => {});
  }, []);

  useEffect(() => {
    const slug = appName.toLowerCase().replace(/[^a-z0-9]/g, '_');
    setPackageName(`com.inspection.${slug || 'app'}`);
  }, [appName]);

  const handleFileDrop = async (file) => {
    setPtFile(file);
    setModelName(file.name.replace(/\.pt$/i, '').replace(/[_-]/g, ' '));
    setAnalyzing(true);
    setClasses([]);
    try {
      const r = await extractClasses(file);
      if (r.data.classes) setClasses(r.data.classes);
    } catch {
      alert('Could not auto-detect classes.');
    } finally {
      setAnalyzing(false);
    }
  };

  const startConvert = async () => {
    if (!ptFile) return;
    setConverting(true);
    setConversionLog('Initializing conversion...\n');
    try {
      const r = await uploadModel(ptFile, modelName || ptFile.name, classes, 640);
      const assetId = r.data.id;
      pollRef.current = setInterval(async () => {
        try {
          const s = await getModelStatus(assetId);
          setConversionLog(s.data.conversion_log || 'Processing...');
          if (s.data.status === 'ready') {
            clearInterval(pollRef.current);
            setConverting(false);
            setSelectedModelIds(prev => [...prev, assetId]);
            setSelectedModelNames(prev => [...prev, modelName || ptFile.name]);
            setPtFile(null);
            setModelName('');
            setClasses([]);
            setConversionLog('');
          }
          if (s.data.status === 'error') {
            clearInterval(pollRef.current);
            setConverting(false);
            alert('Error: ' + s.data.error_message);
          }
        } catch {}
      }, 1500);
    } catch {
      setConverting(false);
      alert('Upload failed');
    }
  };

  const toggleExisting = (m) => {
    if (selectedModelIds.includes(m.id)) {
      setSelectedModelIds(selectedModelIds.filter(id => id !== m.id));
      setSelectedModelNames(selectedModelNames.filter(name => name !== m.vision_project_name));
    } else {
      setSelectedModelIds([...selectedModelIds, m.id]);
      setSelectedModelNames([...selectedModelNames, m.vision_project_name]);
    }
  };

  const handleCreate = async () => {
    try {
      const r = await createApp({
        name: appName || 'Inspection App',
        package_name: packageName,
        model_asset_ids: selectedModelIds,
        app_settings: { app_type: 'sequential', confidence_threshold: 0.5 },
      });
      navigate(`/apps/${r.data.id}`);
    } catch {
      alert('Failed to create app');
    }
  };

  return (
    <div className="newapp-layout">
      {/* ── Main Panel ──────────────────────────────────────── */}
      <div className="newapp-main-panel">

        {/* Step indicator */}
        <div className="newapp-steps-bar">
          <div className={`newapp-step-pill${step === 1 ? ' active' : ' done'}`}>
            {step > 1 ? <Check size={12} /> : '1'}
          </div>
          <div className="newapp-steps-line" />
          <div className={`newapp-step-pill${step === 2 ? ' active' : ''}`}>2</div>
        </div>

        <h2 className="newapp-step-title">
          {step === 1 ? 'Add Models to App' : 'Finalize App Details'}
        </h2>

        {step === 1 && (
          <div className="newapp-step-body">
            <div className="newapp-tabs">
              {['upload', 'existing'].map(t => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={`newapp-tab${tab === t ? ' active' : ''}`}
                >
                  {t === 'upload' ? 'Convert New Model' : 'Select from Library'}
                </button>
              ))}
            </div>

            {tab === 'upload' ? (
              <div className="newapp-upload-body">
                {!converting && <DropZone onFile={handleFileDrop} analyzing={analyzing} />}

                {ptFile && !converting && (
                  <div className="model-preview">
                    <div className="model-preview-field">
                      <label className="section-label">Model Name</label>
                      <input
                        className="field-input"
                        value={modelName}
                        onChange={e => setModelName(e.target.value)}
                        placeholder="e.g. Bumper Detection"
                      />
                    </div>
                    <div className="model-preview-field">
                      <label className="section-label">Detected Classes</label>
                      <div className="model-classes-wrap">
                        {classes.map(c => (
                          <span key={c} className="class-chip">{c}</span>
                        ))}
                      </div>
                    </div>
                    <button className="convert-btn" onClick={startConvert}>
                      <Zap size={15} />
                      Convert & Add to App
                    </button>
                  </div>
                )}

                {converting && (
                  <div className="conversion-log">
                    <pre>{conversionLog}</pre>
                    <div ref={logEndRef} />
                  </div>
                )}
              </div>
            ) : (
              <div className="existing-models-list">
                {existingModels.length === 0 && (
                  <div className="newapp-sidebar-empty" style={{ marginTop: 8 }}>
                    No converted models in library yet.
                  </div>
                )}
                {existingModels.map(m => (
                  <div
                    key={m.id}
                    onClick={() => toggleExisting(m)}
                    className={`existing-model-item${selectedModelIds.includes(m.id) ? ' selected' : ''}`}
                  >
                    <div className="existing-model-head">
                      <span className="existing-model-name">{m.vision_project_name}</span>
                      {selectedModelIds.includes(m.id) && (
                        <span className="existing-model-check">
                          <Check size={14} />
                        </span>
                      )}
                    </div>
                    <div className="existing-model-classes">{m.classes.join(', ')}</div>
                  </div>
                ))}
              </div>
            )}

            <button
              className={`newapp-next-btn${selectedModelIds.length > 0 ? ' enabled' : ' disabled'}`}
              onClick={() => setStep(2)}
              disabled={selectedModelIds.length === 0}
            >
              Next: Configure App Details →
            </button>
          </div>
        )}

        {step === 2 && (
          <div className="newapp-step-body">
            <div>
              <label className="section-label">App Display Name</label>
              <input
                className="field-input"
                value={appName}
                onChange={e => setAppName(e.target.value)}
                placeholder="e.g. Vehicle QC Pro"
              />
            </div>
            <div>
              <label className="section-label">Package Identifier</label>
              <input
                className="field-input"
                value={packageName}
                onChange={e => setPackageName(e.target.value)}
              />
            </div>
            <div className="newapp-step-nav">
              <button className="newapp-back-btn" onClick={() => setStep(1)}>← Back</button>
              <button className="newapp-create-btn" onClick={handleCreate}>
                Create App & Build APK
              </button>
            </div>
          </div>
        )}
      </div>

      {/* ── Sidebar ─────────────────────────────────────────── */}
      <div className="newapp-sidebar">
        <div className="newapp-sidebar-title">Selected Models</div>
        <div className="newapp-sidebar-count">
          {selectedModelNames.length} model{selectedModelNames.length !== 1 ? 's' : ''} added
        </div>

        {selectedModelNames.length === 0 ? (
          <div className="newapp-sidebar-empty">
            No models selected yet.<br />Add models from the left panel.
          </div>
        ) : (
          <div className="newapp-models-stack">
            {selectedModelNames.map((name, i) => (
              <div key={i} className="newapp-model-chip">
                <div className="newapp-model-chip-icon">
                  <Box size={13} />
                </div>
                <span className="newapp-model-label">{name}</span>
              </div>
            ))}
          </div>
        )}

        <div className="newapp-sidebar-hint">
          Mix models from your library or convert new .pt files. All models will be bundled into the APK.
        </div>
      </div>
    </div>
  );
}
