import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { formatAmount } from '../format';
import ProductOptionsPicker, { type OptionGroup } from './ProductOptionsPicker';

type Picked = Record<number, number[]>;

const label = (value: Record<string, string> | undefined, language: string) => {
  if (!value) return '';
  return value[language] || value[language.split('-')[0]] || value['zh-CN'] || value.en
    || Object.values(value).find(Boolean) || '';
};

/**
 * 规格 / 附加选择弹窗。
 *
 * 为什么不放在卡片里：卡片在手机上只有 ~250px 宽（左侧还有分类栏），
 * 内联渲染规格会把商品列表挤成一团。弹窗有整屏宽度，选完再确认，
 * 商品列表只留「名称 + 价格 + 加购」。
 *
 * 弹窗内部的草稿是**局部的**：只有点了「加入购物车」才会写进购物车，
 * 中途关闭不留半成品。
 */
export default function ProductOptionsModal({ product, currency, language, orderable, note, onClose, onConfirm }: {
  product: { id: number; name: Record<string, string>; price_minor: number; image_key?: string; sweetness_enabled: boolean; option_groups?: OptionGroup[] };
  currency: string;
  language: string;
  orderable: boolean;
  note: string;
  onClose: () => void;
  onConfirm: (picked: Picked, sweetness: number | null, quantity: number) => void;
}) {
  const { t } = useTranslation();
  const groups = useMemo(() => product.option_groups ?? [], [product.option_groups]);
  const [picked, setPicked] = useState<Picked>(() => {
    const initial: Picked = {};
    groups.forEach((group) => {
      initial[group.id] = group.options.filter((option) => option.is_default).map((option) => option.id);
    });
    return initial;
  });
  const [sweetness, setSweetness] = useState<number | null>(product.sweetness_enabled ? null : null);
  const [quantity, setQuantity] = useState(1);
  const [error, setError] = useState('');

  // Esc 关闭：弹窗的基本可用性
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const toggle = (group: OptionGroup, optionId: number) => {
    setError('');
    setPicked((current) => {
      const chosen = current[group.id] ?? [];
      let next: number[];
      if (group.multi_select) {
        next = chosen.includes(optionId) ? chosen.filter((id) => id !== optionId) : [...chosen, optionId];
        if (group.max_select && next.length > group.max_select) {
          setError(t('menu.optionMax', { max: group.max_select }));
          return current;
        }
      } else {
        next = group.required && chosen.includes(optionId) ? chosen : [optionId];
      }
      return { ...current, [group.id]: next };
    });
  };

  const delta = groups.reduce((sum, group) => sum
    + group.options.filter((option) => (picked[group.id] ?? []).includes(option.id))
        .reduce((inner, option) => inner + option.price_delta_minor, 0), 0);
  const unitPrice = product.price_minor + delta;

  const confirm = () => {
    if (!orderable) { setError(note || t('menu.notOrderable')); return; }
    const missing = groups.find((group) => group.required && !(picked[group.id] ?? []).length);
    if (missing) {
      setError(t('menu.optionChooseRequired', { name: label(missing.name, language) || missing.name_text }));
      return;
    }
    if (product.sweetness_enabled && sweetness === null) {
      setError(t('menu.selectSweetnessFirst'));
      return;
    }
    onConfirm(picked, sweetness, quantity);
  };

  return <div className="modal-backdrop" onClick={onClose}>
    <div className="modal-sheet" role="dialog" aria-modal="true" aria-label={label(product.name, language)} onClick={(event) => event.stopPropagation()}>
      <header className="modal-head">
        {product.image_key
          ? <img className="modal-image" src={product.image_key} alt="" />
          : <div className="modal-image modal-image-fallback">{label(product.name, language).slice(0, 1)}</div>}
        <div>
          <h3>{label(product.name, language)}</h3>
          <strong>{formatAmount(unitPrice, currency, language)}</strong>
        </div>
        <button type="button" className="modal-close" aria-label={t('common.close')} onClick={onClose}>×</button>
      </header>

      <div className="modal-body">
        {groups.length > 0 && <ProductOptionsPicker groups={groups} value={picked}
                                                    onChange={(groupId, optionId) => {
                                                      const group = groups.find((item) => item.id === groupId);
                                                      if (group) toggle(group, optionId);
                                                    }} currency={currency} />}
        {product.sweetness_enabled && <fieldset className="option-group">
          <legend>{t('menu.sweetness')}<em className="option-required">{t('menu.optionRequired')}</em></legend>
          <div className="option-list">
            {[0, 25, 50, 75, 100].map((value) => <label key={value} className={`option-chip${sweetness === value ? ' option-chip-on' : ''}`}>
              <input type="radio" name={`sweetness-${product.id}`} checked={sweetness === value}
                     onChange={() => { setSweetness(value); setError(''); }} />
              <span>{t(`sweetness.${value}`)}</span>
            </label>)}
          </div>
        </fieldset>}
        {!orderable && <p className="product-window-note">{note}</p>}
        {error && <p className="error" role="alert">{error}</p>}
      </div>

      <footer className="modal-foot">
        <div className="quantity-control">
          <button type="button" onClick={() => setQuantity((value) => Math.max(1, value - 1))} aria-label={t('common.delete')}>−</button>
          <strong>{quantity}</strong>
          <button type="button" onClick={() => setQuantity((value) => Math.min(99, value + 1))} aria-label={t('common.add')}>+</button>
        </div>
        <button className="primary" type="button" disabled={!orderable} onClick={confirm}>
          {t('menu.addToCart')} · {formatAmount(unitPrice * quantity, currency, language)}
        </button>
      </footer>
    </div>
  </div>;
}
