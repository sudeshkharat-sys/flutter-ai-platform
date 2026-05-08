import React from 'react';

/**
 * Reusable confirmation modal.
 *
 * Props:
 *   isOpen       {boolean}   – whether the modal is visible
 *   title        {string}    – modal heading
 *   message      {string}    – body text
 *   confirmLabel {string}    – confirm button text  (default "Confirm")
 *   cancelLabel  {string}    – cancel button text   (default "Cancel")
 *   danger       {boolean}   – use btn-danger style for confirm button
 *   onConfirm    {function}  – called when user clicks confirm
 *   onCancel     {function}  – called when user clicks cancel or overlay
 */
export default function ConfirmModal({
  isOpen,
  title = 'Are you sure?',
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  danger = true,
  onConfirm,
  onCancel,
}) {
  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal-box" style={{ maxWidth: 420 }} onClick={e => e.stopPropagation()}>
        <h3 className="modal-title">{title}</h3>
        {message && <p className="modal-message">{message}</p>}
        <div className="modal-actions">
          <button className="btn-secondary" onClick={onCancel}>
            {cancelLabel}
          </button>
          <button
            className={danger ? 'btn-danger' : 'btn-primary'}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
