import axios from 'axios';

const api = axios.create({ baseURL: 'http://localhost:8001/api/v1' });

/**
 * Upload a .pt model file with its class list.
 * @param {File}     file       The .pt file object
 * @param {string}   modelName  Display name for this model
 * @param {string[]} classes    Array of class name strings  e.g. ["dog","cat"]
 * @param {number}   inputSize  Model input size (default 640)
 */
export const uploadModel = (file, modelName, classes, inputSize = 640) => {
  const form = new FormData();
  form.append('file', file);
  form.append('model_name', modelName);
  form.append('classes', JSON.stringify(classes));
  form.append('input_size', String(inputSize));
  return api.post('/models/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
};

export const extractClasses = (file) => {
  const form = new FormData();
  form.append('file', file);
  return api.post('/models/extract-classes', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
};

export const getModels      = ()         => api.get('/models');
export const getModelStatus = (id) => api.get(`/models/${id}/status`);
export const detectModelClasses = (id) => api.post(`/models/${id}/detect-classes`);
export const deleteModel = (id) => api.delete(`/models/${id}`);


export const getApps    = ()         => api.get('/apps');
export const createApp = (data) => {
  console.log('API: Creating app with data:', data);
  return api.post('/apps', data);
};
export const getApp     = (id)       => api.get(`/apps/${id}`);
export const updateApp = (id, data) => {
  console.log(`API: Updating app ${id} with data:`, data);
  return api.patch(`/apps/${id}`, data);
};
export const deleteApp  = (id)       => api.delete(`/apps/${id}`);

export const exportApp = (id) =>
  api.post(`/apps/${id}/export`, {}, { responseType: 'blob' });

export const buildAPK = (id) => api.post(`/apps/${id}/build`);

export const downloadAPK = (id) =>
  api.get(`/apps/${id}/apk`, { responseType: 'blob' });

// ── Reference Images ─────────────────────────────────────────────────────────
export const uploadReferenceImage = (file) => {
  const form = new FormData();
  form.append('file', file);
  return api.post('/assets/reference-image', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
};
export const getReferenceImageUrl = (filename) =>
  `http://localhost:8001/api/v1/assets/reference-image/${filename}`;

// ── Master Data ──────────────────────────────────────────────────────────────
export const getMasterMappings = () => api.get('/master-data');
export const createMasterMapping = (data) => api.post('/master-data', data);
export const updateMasterMapping = (id, data) => api.put(`/master-data/${id}`, data);
export const deleteMasterMapping = (id) => api.delete(`/master-data/${id}`);

// ── Engine Data ──────────────────────────────────────────────────────────────
export const getEngineMappings = () => api.get('/engine-data');
export const createEngineMapping = (data) => api.post('/engine-data', data);
export const updateEngineMapping = (id, data) => api.put(`/engine-data/${id}`, data);
export const deleteEngineMapping = (id) => api.delete(`/engine-data/${id}`);

export default api;
