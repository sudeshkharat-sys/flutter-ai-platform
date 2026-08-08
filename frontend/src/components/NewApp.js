import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { UploadCloud, Check, Box, Zap } from 'lucide-react';
import { uploadModel, getModelStatus, getModels, createApp, extractClasses, uploadOcrModel } from '../api';
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

  // ── Combined app: one app offering more than one capability (VIN scan /
  // Chakan-engine scan / OCR plate-read), instead of the usual single
  // app_type. Opt-in and separate from every other app_type -- doesn't
  // change how a plain OCR-only or sequential app gets built.
  const [isCombinedApp, setIsCombinedApp] = useState(false);
  const [combinedCapabilities, setCombinedCapabilities] = useState([]); // e.g. ['vin','engine','ocr']
  const toggleCapability = (cap) => {
    setCombinedCapabilities(prev =>
      prev.includes(cap) ? prev.filter(c => c !== cap) : [...prev, cap]
    );
  };

  // ── OCR bundle (pre-built recognizer .tflite, not a .pt to convert) ────
  const [isOcrApp, setIsOcrApp] = useState(false);
  const [ocrEngine, setOcrEngine] = useState('crnn');
  const [ocrTfliteFile, setOcrTfliteFile] = useState(null);
  const [ocrCharsetFile, setOcrCharsetFile] = useState(null);
  const [ocrMetaFile, setOcrMetaFile] = useState(null);
  const [ocrModelName, setOcrModelName] = useState('');
  const [ocrUploading, setOcrUploading] = useState(false);
  // Which detected class is the plate/region box (picked explicitly here
  // instead of guessed in code -- avoids any class-naming mismatch).
  const [ocrRegionClass, setOcrRegionClass] = useState('');
  // Where the truth value the camera read gets checked against comes from:
  // 'none' (OCR only, no pass/fail), 'qr' (scan the engine's QR/barcode
  // sticker), 'type' (one fixed expected string, typed once here).
  const [ocrTruthSource, setOcrTruthSource] = useState('none');
  const [ocrTruthScanType, setOcrTruthScanType] = useState('engine');
  const [ocrExpectedLength, setOcrExpectedLength] = useState('');
  const [ocrTruthText, setOcrTruthText] = useState('');
  const [ocrUseMlkitFallback, setOcrUseMlkitFallback] = useState(false);
  // modelId -> classes[], so the OCR tab can offer a dropdown of every
  // class across whichever detector(s) got added to this app.
  const [modelClassesById, setModelClassesById] = useState({});

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
            setSelectedModelIds(prev => [...prev, assetId]);
            setSelectedModelNames(prev => [...prev, modelName || ptFile.name]);
            setModelClassesById(prev => ({ ...prev, [assetId]: classes }));
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
      setModelClassesById(prev => ({ ...prev, [m.id]: m.classes }));
    }
  };

  // Every class across whichever detector(s) are in this app so far --
  // populates the OCR tab's "which class is the plate/region box" dropdown.
  const availableDetectorClasses = [
    ...new Set(selectedModelIds.flatMap(id => modelClassesById[id] || [])),
  ];

  const submitOcrRecognizer = async () => {
    if (!ocrTfliteFile || !ocrCharsetFile) {
      alert('Both the recognizer .tflite and its charset/labels file are required.');
      return;
    }
    setOcrUploading(true);
    try {
      const name = ocrModelName || ocrTfliteFile.name.replace(/\.tflite$/i, '');
      const r = await uploadOcrModel(
        ocrTfliteFile, ocrCharsetFile, name, `ocr_${ocrEngine}`, ocrMetaFile
      );
      setSelectedModelIds(prev => [...prev, r.data.id]);
      setSelectedModelNames(prev => [...prev, name]);
      setIsOcrApp(true);
      setOcrTfliteFile(null);
      setOcrCharsetFile(null);
      setOcrMetaFile(null);
      setOcrModelName('');
    } catch {
      alert('OCR bundle upload failed');
    } finally {
      setOcrUploading(false);
    }
  };

  const handleCreate = async () => {
    try {
      const ocrSettings = {
        ocr_engine: ocrEngine,
        ocr_region_class: ocrRegionClass,
        ocr_truth_source: ocrTruthSource,
        ocr_truth_scan_type: ocrTruthSource === 'qr' ? ocrTruthScanType : null,
        ocr_expected_length: ocrExpectedLength ? parseInt(ocrExpectedLength, 10) : null,
        ocr_truth_text: ocrTruthSource === 'type' ? ocrTruthText : null,
        ocr_use_mlkit_fallback: ocrUseMlkitFallback,
      };
      let app_settings;
      if (isCombinedApp) {
        app_settings = {
          app_type: 'combined',
          combined_capabilities: combinedCapabilities,
          confidence_threshold: 0.5,
          ...(combinedCapabilities.includes('ocr') ? ocrSettings : {}),
        };
      } else if (isOcrApp) {
        app_settings = { app_type: 'ocr', confidence_threshold: 0.5, ...ocrSettings };
      } else {
        app_settings = { app_type: 'sequential', confidence_threshold: 0.5 };
      }
      const r = await createApp({
        name: appName || 'Inspection App',
        package_name: packageName,
        model_asset_ids: selectedModelIds,
        app_settings,
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
            <div className="model-preview-field" style={{ marginBottom: 4 }}>
              <label className="section-label">
                <input
                  type="checkbox"
                  checked={isCombinedApp}
                  onChange={e => setIsCombinedApp(e.target.checked)}
                  style={{ marginRight: 6 }}
                />
                Combined app -- offer more than one capability in this app
              </label>
              {isCombinedApp && (
                <div style={{ display: 'flex', gap: 16, marginTop: 8 }}>
                  {[
                    { key: 'vin', label: 'VIN scan' },
                    { key: 'engine', label: 'Chakan / Engine scan' },
                    { key: 'ocr', label: 'OCR plate read' },
                    { key: 'inspection', label: 'Class Inspection (masterdata mapping)' },
                  ].map(cap => (
                    <label key={cap.key} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                      <input
                        type="checkbox"
                        checked={combinedCapabilities.includes(cap.key)}
                        onChange={() => toggleCapability(cap.key)}
                      />
                      {cap.label}
                    </label>
                  ))}
                </div>
              )}
              {isCombinedApp && combinedCapabilities.includes('ocr') && (
                <p className="newapp-sidebar-hint" style={{ marginTop: 6 }}>
                  Configure OCR (region class, expected text source, recognizer upload) in the
                  "OCR Bundle" tab below, same as a plain OCR app.
                </p>
              )}
              {isCombinedApp && combinedCapabilities.includes('inspection') && (
                <p className="newapp-sidebar-hint" style={{ marginTop: 6 }}>
                  Configure the class checklist, mandatory classes, and masterdata mapping in the
                  app's "Add Inspection Profile" screen after creating it, same as a plain
                  inspection app.
                </p>
              )}
            </div>

            <div className="newapp-tabs">
              {['upload', 'existing', 'ocr'].map(t => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={`newapp-tab${tab === t ? ' active' : ''}`}
                >
                  {t === 'upload' ? 'Convert New Model' : t === 'existing' ? 'Select from Library' : 'OCR Bundle'}
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
            ) : tab === 'existing' ? (
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
            ) : null}

            {tab === 'ocr' && (
              <div className="newapp-upload-body">
                <p className="newapp-sidebar-hint" style={{ marginBottom: 12 }}>
                  Add the plate/character <strong>detector</strong> from the other two tabs
                  first (it's a normal YOLO .pt model). Then upload the OCR{' '}
                  <strong>recognizer</strong> here — the .tflite exported from
                  ai-vision-platform's OCR trainer, plus its charset.txt/labels.txt.
                </p>

                <div className="model-preview-field">
                  <label className="section-label">Recognizer engine</label>
                  <div style={{ display: 'flex', gap: 12 }}>
                    <label>
                      <input
                        type="radio"
                        checked={ocrEngine === 'crnn'}
                        onChange={() => setOcrEngine('crnn')}
                      /> CRNN + CTC (line reader)
                    </label>
                    <label>
                      <input
                        type="radio"
                        checked={ocrEngine === 'cnn'}
                        onChange={() => setOcrEngine('cnn')}
                      /> Per-character CNN
                    </label>
                  </div>
                </div>

                <div className="model-preview-field">
                  <label className="section-label">
                    Plate / region class{' '}
                    {ocrEngine === 'cnn' && <span style={{ opacity: 0.6 }}>(not used by the CNN engine)</span>}
                  </label>
                  {availableDetectorClasses.length === 0 ? (
                    <div className="newapp-sidebar-hint">
                      Add the detector .pt first (other two tabs) — its classes will show up here to pick from.
                    </div>
                  ) : (
                    <select
                      className="field-input"
                      value={ocrRegionClass}
                      onChange={e => setOcrRegionClass(e.target.value)}
                    >
                      <option value="">(auto — none matched exactly)</option>
                      {availableDetectorClasses.map(c => (
                        <option key={c} value={c}>{c}</option>
                      ))}
                    </select>
                  )}
                  <p className="newapp-sidebar-hint" style={{ marginTop: 4 }}>
                    Which detected class is the whole-plate box (used when individual
                    character boxes aren't found, so the CRNN reads that crop instead of
                    the full photo). Pick it explicitly here rather than relying on the
                    class being named exactly "PLATE".
                  </p>
                </div>

                <div className="model-preview-field">
                  <label className="section-label">Expected text source (truth value)</label>
                  <select
                    className="field-input"
                    value={ocrTruthSource}
                    onChange={e => setOcrTruthSource(e.target.value)}
                  >
                    <option value="none">None -- OCR only, no pass/fail</option>
                    <option value="qr">Scan a QR/barcode (e.g. engine number sticker)</option>
                    <option value="type">Type one fixed expected value</option>
                  </select>

                  {ocrTruthSource === 'qr' && (
                    <>
                      <select
                        className="field-input"
                        value={ocrTruthScanType}
                        onChange={e => setOcrTruthScanType(e.target.value)}
                        style={{ marginTop: 8 }}
                      >
                        <option value="engine">Engine number sticker (PART_NO SERIAL_NO)</option>
                        <option value="model">VIN plate scan (17-char VIN + model code)</option>
                        <option value="chakan">Chakan Plant sticker (VIN_MODELCODE_GARBAGE)</option>
                      </select>

                      {ocrTruthScanType === 'engine' && (
                        <>
                          <input
                            className="field-input"
                            type="number"
                            min="1"
                            value={ocrExpectedLength}
                            onChange={e => setOcrExpectedLength(e.target.value)}
                            placeholder="Expected code length (e.g. 10) -- optional"
                            style={{ marginTop: 8 }}
                          />
                          <p className="newapp-sidebar-hint" style={{ marginTop: 4 }}>
                            Reuses the platform's existing engine-code barcode scan: "PART_NO SERIAL_NO"
                            gets validated against your Engine Data list; a code with no space falls back
                            to the first N characters as the truth value (needs the length above).
                          </p>
                        </>
                      )}
                      {ocrTruthScanType === 'model' && (
                        <p className="newapp-sidebar-hint" style={{ marginTop: 4 }}>
                          Reuses the platform's existing VIN scan: the 17-character VIN plus model-code
                          suffix gets validated against Master Data, then the VIN itself becomes the
                          truth value the OCR read is checked against.
                        </p>
                      )}
                      {ocrTruthScanType === 'chakan' && (
                        <p className="newapp-sidebar-hint" style={{ marginTop: 4 }}>
                          Reuses the platform's existing Chakan Plant scan ("VIN_MODELCODE_GARBAGE"):
                          the model code gets validated against Master Data, then the VIN becomes the
                          truth value the OCR read is checked against.
                        </p>
                      )}
                    </>
                  )}

                  {ocrTruthSource === 'type' && (
                    <input
                      className="field-input"
                      value={ocrTruthText}
                      onChange={e => setOcrTruthText(e.target.value)}
                      placeholder="Expected text, e.g. ABC123"
                      style={{ marginTop: 8 }}
                    />
                  )}

                  {ocrTruthSource !== 'none' && (
                    <label style={{ display: 'block', marginTop: 8 }}>
                      <input
                        type="checkbox"
                        checked={ocrUseMlkitFallback}
                        onChange={e => setOcrUseMlkitFallback(e.target.checked)}
                        style={{ marginRight: 6 }}
                      />
                      Fall back to Google ML Kit if the camera read doesn't match
                    </label>
                  )}
                </div>

                <div className="model-preview-field">
                  <label className="section-label">Recognizer Name</label>
                  <input
                    className="field-input"
                    value={ocrModelName}
                    onChange={e => setOcrModelName(e.target.value)}
                    placeholder="e.g. Engine Plate CRNN"
                  />
                </div>

                <div className="model-preview-field">
                  <label className="section-label">Recognizer .tflite</label>
                  <input
                    type="file"
                    accept=".tflite"
                    onChange={e => setOcrTfliteFile(e.target.files[0])}
                  />
                </div>

                <div className="model-preview-field">
                  <label className="section-label">charset.txt / labels.txt</label>
                  <input
                    type="file"
                    accept=".txt"
                    onChange={e => setOcrCharsetFile(e.target.files[0])}
                  />
                </div>

                <div className="model-preview-field">
                  <label className="section-label">meta.json (optional)</label>
                  <input
                    type="file"
                    accept=".json"
                    onChange={e => setOcrMetaFile(e.target.files[0])}
                  />
                </div>

                <button
                  className="convert-btn"
                  onClick={submitOcrRecognizer}
                  disabled={ocrUploading || !ocrTfliteFile || !ocrCharsetFile}
                >
                  <Zap size={15} />
                  {ocrUploading ? 'Uploading...' : 'Add Recognizer to App'}
                </button>
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
