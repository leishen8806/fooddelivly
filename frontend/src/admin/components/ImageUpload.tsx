import { useRef, useState, type ChangeEvent } from 'react';
import { useTranslation } from 'react-i18next';
import api from '../../api';

export default function ImageUpload({ label: fieldLabel, value, onChange }: {
  label: string;
  value: string | null;
  onChange: (url: string | null) => void;
}) {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');

  const selectImage = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setError('');
    if (file.size > 5 * 1024 * 1024) {
      setError(t('admin.uploadTooLarge'));
      event.target.value = '';
      return;
    }
    const form = new FormData();
    form.append('file', file);
    setUploading(true);
    try {
      const response = await api.post('/api/v1/admin/uploads', form);
      onChange(response.data.url);
    } catch {
      setError(t('admin.uploadFailed'));
    } finally {
      setUploading(false);
      event.target.value = '';
    }
  };

  return <div className="image-upload">
    <span className="image-upload-label">{fieldLabel}</span>
    <div className="image-upload-row">
      {value && <img className="image-upload-preview" src={value} alt={fieldLabel} />}
      <input ref={inputRef} className="image-upload-input" type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => void selectImage(event)} />
      <button type="button" disabled={uploading} onClick={() => inputRef.current?.click()}>{uploading ? t('admin.uploadingImage') : t('admin.chooseImage')}</button>
      {value && <button type="button" disabled={uploading} onClick={() => onChange(null)}>{t('admin.removeImage')}</button>}
    </div>
    <small>{t('admin.imageUploadHint')}</small>
    {error && <span className="error" role="alert">{error}</span>}
  </div>;
}
