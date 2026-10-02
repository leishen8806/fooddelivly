import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import api from '../api';
import { toMinorUnits } from '../format';

type SaleWindowRow = { start: string; end: string };
type OptionRow = { nameEn: string; nameZh: string; nameKm: string; price: string; isDefault: boolean; active: boolean };
type GroupRow = {
  nameEn: string; nameZh: string; nameKm: string;
  kind: 'SPEC' | 'ADDON'; required: boolean; multiSelect: boolean; maxSelect: string; active: boolean;
  options: OptionRow[];
};

const emptyOption = (): OptionRow => ({ nameEn: '', nameZh: '', nameKm: '', price: '0', isDefault: false, active: true });
const emptyGroup = (): GroupRow => ({
  nameEn: '', nameZh: '', nameKm: '', kind: 'SPEC', required: true, multiSelect: false,
  maxSelect: '', active: true, options: [emptyOption()],
});

/**
 * 菜品的「售卖时间」与「规格 / 附加」编辑器（管理端）。
 *
 * 两个接口都是**整体替换**语义：编辑完整表单再保存，不会出现「组删了选项还在」
 * 的中间状态。历史订单保存的是下单时的名称与加价快照，所以重建分组不影响旧订单。
 */
export default function AdminProductRules({ productId, productName, currency, initialWindows, initialGroups, onSaved }: {
  productId: number;
  productName: string;
  currency: string;
  initialWindows: SaleWindowRow[];
  initialGroups: GroupRow[];
  onSaved: () => Promise<void> | void;
}) {
  const { t } = useTranslation();
  const [windows, setWindows] = useState<SaleWindowRow[]>(initialWindows);
  const [groups, setGroups] = useState<GroupRow[]>(initialGroups);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');

  const saveWindows = async () => {
    setBusy(true); setNotice('');
    try {
      await api.put(`/api/v1/admin/products/${productId}/sale-windows`, { windows });
      setNotice(t('admin.rulesSaved'));
      await onSaved();
    } catch (error: any) {
      setNotice(error?.response?.data?.detail || t('error.network'));
    } finally { setBusy(false); }
  };

  const saveGroups = async () => {
    setBusy(true); setNotice('');
    try {
      await api.put(`/api/v1/admin/products/${productId}/option-groups`, {
        groups: groups.map((group) => ({
          name: { en: group.nameEn, 'zh-CN': group.nameZh || undefined, km: group.nameKm || undefined },
          kind: group.kind,
          required: group.required,
          multi_select: group.kind === 'ADDON' ? group.multiSelect : false,
          max_select: group.kind === 'ADDON' && group.maxSelect ? Number(group.maxSelect) : null,
          active: group.active,
          options: group.options.map((option) => ({
            name: { en: option.nameEn, 'zh-CN': option.nameZh || undefined, km: option.nameKm || undefined },
            price_delta_minor: toMinorUnits(Number(option.price || 0), currency),
            is_default: option.isDefault,
            active: option.active,
          })),
        })),
      });
      setNotice(t('admin.rulesSaved'));
      await onSaved();
    } catch (error: any) {
      setNotice(error?.response?.data?.detail || t('error.network'));
    } finally { setBusy(false); }
  };

  const patchGroup = (index: number, patch: Partial<GroupRow>) =>
    setGroups((current) => current.map((group, i) => (i === index ? { ...group, ...patch } : group)));
  const patchOption = (groupIndex: number, optionIndex: number, patch: Partial<OptionRow>) =>
    setGroups((current) => current.map((group, i) => i === groupIndex
      ? { ...group, options: group.options.map((option, j) => (j === optionIndex ? { ...option, ...patch } : option)) }
      : group));

  return <div className="rules-editor" data-product={productId}>
    <h4>{productName} · {t('admin.saleWindows')}</h4>
    <p className="rules-hint">{t('admin.saleWindowsHint')}</p>
    <div className="rules-rows">
      {windows.map((window, index) => <div className="rules-row" key={index}>
        <input type="time" value={window.start} onChange={(event) =>
          setWindows((current) => current.map((w, i) => i === index ? { ...w, start: event.target.value } : w))} />
        <span>–</span>
        <input type="time" value={window.end} onChange={(event) =>
          setWindows((current) => current.map((w, i) => i === index ? { ...w, end: event.target.value } : w))} />
        <button type="button" onClick={() => setWindows((current) => current.filter((_, i) => i !== index))}>
          {t('common.delete')}
        </button>
      </div>)}
    </div>
    <div className="actions">
      <button type="button" onClick={() => setWindows((current) => [...current, { start: '08:00', end: '20:00' }])}>
        {t('admin.addWindow')}
      </button>
      <button className="primary" type="button" disabled={busy} onClick={() => void saveWindows()}>{t('common.save')}</button>
    </div>

    <h4>{t('admin.optionGroups')}</h4>
    <p className="rules-hint">{t('admin.optionGroupsHint')}</p>
    {groups.map((group, groupIndex) => <fieldset className="rules-group" key={groupIndex}>
      <legend>{group.nameEn || t('admin.optionGroup')} #{groupIndex + 1}</legend>
      <div className="rules-grid">
        <label>{t('admin.optionGroupName')} (EN)<input value={group.nameEn} onChange={(e) => patchGroup(groupIndex, { nameEn: e.target.value })} /></label>
        <label>{t('admin.optionGroupName')} (中文)<input value={group.nameZh} onChange={(e) => patchGroup(groupIndex, { nameZh: e.target.value })} /></label>
        <label>{t('admin.optionGroupName')} (ខ្មែរ)<input value={group.nameKm} onChange={(e) => patchGroup(groupIndex, { nameKm: e.target.value })} /></label>
        <label>{t('admin.optionKind')}<select value={group.kind} onChange={(e) => patchGroup(groupIndex, { kind: e.target.value as 'SPEC' | 'ADDON' })}>
          <option value="SPEC">{t('admin.optionKindSpec')}</option>
          <option value="ADDON">{t('admin.optionKindAddon')}</option>
        </select></label>
        <label className="rules-check"><input type="checkbox" checked={group.required} onChange={(e) => patchGroup(groupIndex, { required: e.target.checked })} />{t('admin.optionRequired')}</label>
        {group.kind === 'ADDON' && <>
          <label className="rules-check"><input type="checkbox" checked={group.multiSelect} onChange={(e) => patchGroup(groupIndex, { multiSelect: e.target.checked })} />{t('admin.optionMulti')}</label>
          <label>{t('admin.optionMaxSelect')}<input type="number" min="1" max="50" value={group.maxSelect} onChange={(e) => patchGroup(groupIndex, { maxSelect: e.target.value })} /></label>
        </>}
      </div>
      {group.options.map((option, optionIndex) => <div className="rules-row rules-option" key={optionIndex}>
        <input placeholder="EN" value={option.nameEn} onChange={(e) => patchOption(groupIndex, optionIndex, { nameEn: e.target.value })} />
        <input placeholder="中文" value={option.nameZh} onChange={(e) => patchOption(groupIndex, optionIndex, { nameZh: e.target.value })} />
        <input placeholder="ខ្មែរ" value={option.nameKm} onChange={(e) => patchOption(groupIndex, optionIndex, { nameKm: e.target.value })} />
        <label>{t('admin.optionPrice')}<input type="number" step="0.01" value={option.price} onChange={(e) => patchOption(groupIndex, optionIndex, { price: e.target.value })} /></label>
        <label className="rules-check"><input type="checkbox" checked={option.isDefault} onChange={(e) => patchOption(groupIndex, optionIndex, { isDefault: e.target.checked })} />{t('admin.optionDefault')}</label>
        <label className="rules-check"><input type="checkbox" checked={option.active} onChange={(e) => patchOption(groupIndex, optionIndex, { active: e.target.checked })} />{t('common.available')}</label>
        <button type="button" onClick={() => patchGroup(groupIndex, { options: group.options.filter((_, j) => j !== optionIndex) })}>{t('common.delete')}</button>
      </div>)}
      <div className="actions">
        <button type="button" onClick={() => patchGroup(groupIndex, { options: [...group.options, emptyOption()] })}>{t('admin.addOption')}</button>
        <button type="button" onClick={() => setGroups((current) => current.filter((_, i) => i !== groupIndex))}>{t('admin.removeGroup')}</button>
      </div>
    </fieldset>)}
    <div className="actions">
      <button type="button" onClick={() => setGroups((current) => [...current, emptyGroup()])}>{t('admin.addGroup')}</button>
      <button className="primary" type="button" disabled={busy} onClick={() => void saveGroups()}>{t('common.save')}</button>
    </div>
    {notice && <p className="status" role="status">{notice}</p>}
  </div>;
}
