import { useTranslation } from 'react-i18next';
import { formatAmount } from '../format';

export type OptionItem = {
  id: number;
  name: Record<string, string>;
  name_text: string;
  price_delta_minor: number;
  is_default: boolean;
};

export type OptionGroup = {
  id: number;
  name: Record<string, string>;
  name_text: string;
  kind: string;
  required: boolean;
  multi_select: boolean;
  min_select: number;
  max_select: number | null;
  options: OptionItem[];
};

type Picked = Record<number, number[]>;   // groupId -> optionIds

/**
 * 菜品规格 / 附加选择器（参考美团外卖的交互）。
 *
 * 只是「让用户能选」，**价格和校验都以服务端为准**：这里算出来的金额只用于
 * 展示，下单时只提交 group_id / option_id。
 */
export default function ProductOptionsPicker({ groups, value, onChange, currency }: {
  groups: OptionGroup[];
  value: Picked;
  onChange: (groupId: number, optionId: number) => void;
  currency: string;
}) {
  const { t, i18n } = useTranslation();
  if (!groups.length) return null;

  // 名称要用**顾客界面语言**取：服务端的 name_text 是按员工群语言算的，
  // 直接用它会让中文顾客看到英文规格名。
  const localize = (name: Record<string, string> | undefined, fallback?: string) => {
    const lang = i18n.language || 'en';
    return name?.[lang] || name?.[lang.split('-')[0]] || name?.['zh-CN'] || name?.en || fallback || '';
  };

  return <div className="option-groups">
    {groups.map((group) => {
      const picked = value[group.id] ?? [];
      return <fieldset className="option-group" key={group.id}>
        <legend>
          {localize(group.name, group.name_text)}
          {group.required && <em className="option-required">{t('menu.optionRequired')}</em>}
          {group.multi_select && group.max_select
            ? <small>{t('menu.optionMax', { max: group.max_select })}</small>
            : null}
        </legend>
        <div className="option-list">
          {group.options.map((option) => {
            const checked = picked.includes(option.id);
            return <label key={option.id} className={`option-chip${checked ? ' option-chip-on' : ''}`}>
              <input
                type={group.multi_select ? 'checkbox' : 'radio'}
                name={`group-${group.id}`}
                checked={checked}
                onChange={() => onChange(group.id, option.id)}
              />
              <span>{localize(option.name, option.name_text)}</span>
              {option.price_delta_minor !== 0 && <b>
                {option.price_delta_minor > 0 ? '+' : '−'}
                {formatAmount(Math.abs(option.price_delta_minor), currency, i18n.language)}
              </b>}
            </label>;
          })}
        </div>
      </fieldset>;
    })}
  </div>;
}
