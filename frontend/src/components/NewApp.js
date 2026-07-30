import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { UploadCloud, Check, Box, Zap } from 'lucide-react';
import { uploadModel, getModelStatus, getModels, createApp, extractClasses } from '../api';
import '../styles/NewApp.css';

function DropZone({ onFile, analyzing, accept = '.pt', title = 'Upload YOLO .pt model', sub = 'Drag & drop or click to browse — classes auto-detected' }) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef();

  const handle = useCallback(
    (f) => {
      if (!f) return;
      const exts = accept.split(',').map(e => e.trim());
      if (exts.some(ext => f.name.toLowerCase().endsWith(ext))) onFile(f);
    },
    [onFile, accept]
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
        accept={accept}
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
          <div className="drop-zone-title">{title}</div>
          <div className="drop-zone-sub">{sub}</div>
        </>
      )}
    </div>
  );
}

// ── Direct .tflite uploader ──────────────────────────────────────────────────
// For models that are already exported to TFLite outside the YOLO/.pt
// pipeline (e.g. a CRNN character reader) — no class auto-detection, no
// conversion step, the file is usable as soon as the upload finishes.
function TfliteUploader({ onUploaded }) {
  const [file, setFile] = useState(null);
  const [name, setName] = useState('');
  const [classesText, setClassesText] = useState('');
  const [uploading, setUploading] = useState(false);

  const handleFile = (f) => {
    setFile(f);
    setName(f.name.replace(/\.tflite$/i, '').replace(/[_-]/g, ' '));
  };

  const doUpload = async () => {
    if (!file) return;
    setUploading(true);
    try {
      const classes = classesText.split(',').map(c => c.trim()).filter(Boolean);
      const r = await uploadModel(file, name || file.name, classes);
      onUploaded(r.data);
      setFile(null);
      setName('');
      setClassesText('');
    } catch {
      alert('Upload failed');
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="newapp-upload-body">
      {!file && (
        <DropZone
          onFile={handleFile}
          analyzing={false}
          accept=".tflite"
          title="Upload .tflite model"
          sub="Already-converted model — no conversion needed"
        />
      )}
      {file && (
        <div className="model-preview">
          <div className="model-preview-field">
            <label className="section-label">Model Name</label>
            <input
              className="field-input"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="e.g. Char Reader CRNN"
            />
          </div>
          <div className="model-preview-field">
            <label className="section-label">Classes (optional, comma-separated)</label>
            <input
              className="field-input"
              value={classesText}
              onChange={e => setClassesText(e.target.value)}
              placeholder="e.g. 0,1,2,...,A,B,C (leave blank if not needed)"
            />
          </div>
          <button className="convert-btn" onClick={doUpload} disabled={uploading}>
            <Zap size={15} />
            {uploading ? 'Uploading...' : 'Upload & Use'}
          </button>
        </div>
      )}
    </div>
  );
}

export default function NewApp() {
  const navigate = useNavigate();
  const [step, setStep] = useState(1);
  const [tab, setTab] = useState('upload');

  // App type: 'sequential' (existing inspection/VIN-scan builder, unchanged)
  // or 'free_ocr' (standalone YOLO-detect + CRNN-read character reader —
  // skips canvas/inspection-task config entirely, just needs two models).
  const [appType, setAppType] = useState('sequential');
  const [detectorModelId, setDetectorModelId] = useState('');
  const [recognizerModelId, setRecognizerModelId] = useState('');
  const [detectorSubTab, setDetectorSubTab] = useState('existing');
  const [recognizerSubTab, setRecognizerSubTab] = useState('existing');

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
    let slug = appName.toLowerCase().replace(/[^a-z0-9]/g, '_');
    if (!slug) slug = 'app';
    // Java package segments can't start with a digit (e.g. "4x4_reverse").
    if (/^[0-9]/.test(slug)) slug = `app_${slug}`;
    setPackageName(`com.inspection.${slug}`);
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
            setExistingModels(prev => [...prev, { id: assetId, vision_project_name: modelName || ptFile.name, status: 'ready', classes }]);
            if (isFreeOcr) {
              setDetectorModelId(assetId);
              setDetectorSubTab('existing');
            } else {
              setSelectedModelIds(prev => [...prev, assetId]);
              setSelectedModelNames(prev => [...prev, modelName || ptFile.name]);
            }
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
      const payload = isFreeOcr
        ? {
            name: appName || 'OCR Reader App',
            package_name: packageName,
            model_asset_ids: Array.from(new Set([detectorModelId, recognizerModelId].filter(Boolean))),
            app_settings: {
              app_type: 'free_ocr',
              detector_model_id: detectorModelId,
              recognizer_model_id: recognizerModelId,
            },
          }
        : {
            name: appName || 'Inspection App',
            package_name: packageName,
            model_asset_ids: selectedModelIds,
            app_settings: { app_type: 'sequential', confidence_threshold: 0.5 },
          };
      const r = await createApp(payload);
      navigate(`/apps/${r.data.id}`);
    } catch {
      alert('Failed to create app');
    }
  };

  // free_ocr apps only need two model slots (detector + recognizer) — no
  // canvas/widget builder, no inspection_tasks/mandatoryClasses/classOcrConfig,
  // no VIN/master-data config, so step 1 shows two model-slot pickers instead
  // of the upload/existing-model picker used by the sequential inspection flow.
  const isFreeOcr = appType === 'free_ocr';
  const freeOcrReady = !!detectorModelId && !!recognizerModelId;

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
              {['sequential', 'free_ocr'].map(t => (
                <button
                  key={t}
                  onClick={() => setAppType(t)}
                  className={`newapp-tab${appType === t ? ' active' : ''}`}
                >
                  {t === 'sequential' ? 'Inspection App' : 'OCR Reader'}
                </button>
              ))}
            </div>

            {isFreeOcr ? (
              <div className="newapp-upload-body">
                {/* ── Detector slot: pick existing YOLO model or upload/convert a new .pt ── */}
                <div className="model-preview-field">
                  <label className="section-label">Plate/Region Detector (YOLO)</label>
                  <div className="newapp-tabs" style={{ marginBottom: 8 }}>
                    {['existing', 'upload'].map(t => (
                      <button
                        key={t}
                        onClick={() => setDetectorSubTab(t)}
                        className={`newapp-tab${detectorSubTab === t ? ' active' : ''}`}
                      >
                        {t === 'existing' ? 'Select from Library' : 'Upload .pt'}
                      </button>
                    ))}
                  </div>
                  {detectorSubTab === 'existing' ? (
                    <select
                      className="field-input"
                      value={detectorModelId}
                      onChange={e => setDetectorModelId(e.target.value)}
                    >
                      <option value="">Select a model...</option>
                      {existingModels.map(m => (
                        <option key={m.id} value={m.id}>{m.vision_project_name}</option>
                      ))}
                    </select>
                  ) : (
                    <div>
                      {!converting && <DropZone onFile={handleFileDrop} analyzing={analyzing} accept=".pt" />}
                      {ptFile && !converting && (
                        <div className="model-preview">
                          <div className="model-preview-field">
                            <label className="section-label">Model Name</label>
                            <input
                              className="field-input"
                              value={modelName}
                              onChange={e => setModelName(e.target.value)}
                              placeholder="e.g. Plate Detector"
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
                            Convert & Use
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
                  )}
                </div>

                {/* ── Recognizer slot: pick existing CRNN model or upload a ready .tflite ── */}
                <div className="model-preview-field">
                  <label className="section-label">Character Reader (CRNN)</label>
                  <div className="newapp-tabs" style={{ marginBottom: 8 }}>
                    {['existing', 'upload'].map(t => (
                      <button
                        key={t}
                        onClick={() => setRecognizerSubTab(t)}
                        className={`newapp-tab${recognizerSubTab === t ? ' active' : ''}`}
                      >
                        {t === 'existing' ? 'Select from Library' : 'Upload .tflite'}
                      </button>
                    ))}
                  </div>
                  {recognizerSubTab === 'existing' ? (
                    <select
                      className="field-input"
                      value={recognizerModelId}
                      onChange={e => setRecognizerModelId(e.target.value)}
                    >
                      <option value="">Select a model...</option>
                      {existingModels.map(m => (
                        <option key={m.id} value={m.id}>{m.vision_project_name}</option>
                      ))}
                    </select>
                  ) : (
                    <TfliteUploader
                      onUploaded={(asset) => {
                        setExistingModels(prev => [...prev, asset]);
                        setRecognizerModelId(asset.id);
                        setRecognizerSubTab('existing');
                      }}
                    />
                  )}
                </div>

                {existingModels.length === 0 && (
                  <div className="newapp-sidebar-empty" style={{ marginTop: 8 }}>
                    No converted models in library yet. Upload a YOLO detector (.pt) and a
                    CRNN reader (.tflite) above.
                  </div>
                )}
              </div>
            ) : (
              <>
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
              </>
            )}

            <button
              className={`newapp-next-btn${(isFreeOcr ? freeOcrReady : selectedModelIds.length > 0) ? ' enabled' : ' disabled'}`}
              onClick={() => setStep(2)}
              disabled={isFreeOcr ? !freeOcrReady : selectedModelIds.length === 0}
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
          {isFreeOcr
            ? `${[detectorModelId, recognizerModelId].filter(Boolean).length} of 2 model slots set`
            : `${selectedModelNames.length} model${selectedModelNames.length !== 1 ? 's' : ''} added`}
        </div>

        {isFreeOcr ? (
          freeOcrReady ? (
            <div className="newapp-models-stack">
              {[
                { label: 'Detector', id: detectorModelId },
                { label: 'Recognizer', id: recognizerModelId },
              ].map(({ label, id }) => (
                <div key={label} className="newapp-model-chip">
                  <div className="newapp-model-chip-icon">
                    <Box size={13} />
                  </div>
                  <span className="newapp-model-label">
                    {label}: {existingModels.find(m => m.id === id)?.vision_project_name || id}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="newapp-sidebar-empty">
              Pick or upload a detector and a recognizer model from the left panel.
            </div>
          )
        ) : selectedModelNames.length === 0 ? (
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
