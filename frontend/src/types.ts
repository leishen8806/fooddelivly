export interface LocalizedText {
  en: string;
  'zh-CN'?: string;
  km?: string;
}

export interface Category {
  id: number;
  name: LocalizedText;
  sort_order: number;
  active: boolean;
}

export interface Product {
  id: number;
  category_id: number;
  category_ids?: number[];
  name: LocalizedText;
  description?: LocalizedText;
  price_minor: number;
  currency: string;
  image_key?: string;
  available: boolean;
}

export interface OrderItem {
  product_id: number;
  quantity: number;
  options?: any;
}

export interface Order {
  id: number;
  public_code: string;
  room_number: string;
  order_status: string;
  payment_status: string;
  total_minor: number;
  currency: string;
}

/** 钱包余额概览（GET /api/v1/wallet） */
export interface WalletSummary {
  currency: string;
  /** 余额 = 本金 + 赠送 + 冻结（账户总额） */
  balance_minor: number;
  /** 可用 = 本金 + 赠送（现在能花的部分） */
  available_minor: number;
  /** 冻结（预留提现/风控，目前恒为 0） */
  frozen_minor: number;
  principal_minor: number;
  bonus_minor: number;
  /** 与 available_minor 同值，保留给既有调用方 */
  total_minor: number;
  bonus_expire_at: string | null;
  presets_minor: number[];
  min_recharge_minor: number;
  max_recharge_minor: number;
  max_open_orders: number;
}

/** 充值单。所有金额都是最小货币单位（USD cents）。 */
export interface RechargeOrder {
  id: number;
  order_no: string;
  currency: string;
  amount_minor: number;
  bonus_amount_minor: number;
  status: 'awaiting_proof' | 'under_review' | 'credited' | 'rejected' | 'expired' | 'cancelled';
  received_amount_minor: number | null;
  pending_received_amount_minor?: number | null;
  proof_count: number;
  pay_reference: string | null;
  expires_at: string;
  submitted_at: string | null;
  reviewed_at: string | null;
  reject_reason: string | null;
  created_at: string;
  payment_link?: string | null;
  payment_qr_url?: string | null;
  bot_deeplink?: string | null;
  customer_id?: number;
  telegram_user_id?: string;
  display_name?: string | null;
  username?: string | null;
  /** 大额双人复核：已确认人数 / 需要人数（>1 表示要走双人复核） */
  approval_count?: number;
  required_approvals?: number;
  /** 客户**当前**钱包余额（管理端列表用，LEFT JOIN wallets 得到，没有钱包为 0） */
  balance_minor?: number;
  principal_minor?: number;
  bonus_minor?: number;
}

/** 账本流水（只读） */
export interface LedgerEntry {
  id: number;
  currency?: string;
  bucket: 'principal' | 'bonus';
  direction: 1 | -1;
  amount_minor: number;
  balance_after_minor: number;
  entry_type: string;
  biz_type: string | null;
  biz_id: string | null;
  remark: string | null;
  created_at: string;
}
