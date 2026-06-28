export interface MobileSite {
  key: string;
  name: string;
  loginUrl: string;
  accountPages: string[];
  emoji: string;
}

export const MOBILE_SITES: MobileSite[] = [
  // ── Airlines ───────────────────────────────────────────────────────────────
  {
    key: 'delta',
    name: 'Delta Air Lines',
    emoji: '✈️',
    loginUrl: 'https://www.delta.com/us/en/sign-in/start',
    accountPages: [
      'https://www.delta.com/myprofile/',
      'https://www.delta.com/my-profile/certificates',
      'https://www.delta.com/us/en/my-account/overview',
    ],
  },
  {
    key: 'united',
    name: 'United Airlines',
    emoji: '✈️',
    loginUrl: 'https://www.united.com/ux/en/login',
    accountPages: [
      'https://www.united.com/en/us/myunited',
      'https://www.united.com/en/US/mileageplus/account',
      'https://www.united.com/en/us/mileageplus',
    ],
  },
  {
    key: 'southwest',
    name: 'Southwest Airlines',
    emoji: '✈️',
    loginUrl: 'https://www.southwest.com/account/login',
    accountPages: [
      'https://www.southwest.com/loyalty/myaccount/',
      'https://www.southwest.com/rapid-rewards/',
    ],
  },
  {
    key: 'american_air',
    name: 'American Airlines',
    emoji: '✈️',
    loginUrl: 'https://www.aa.com/login.do',
    accountPages: [
      'https://www.aa.com/loyalty/home.do',
      'https://www.aa.com/myprofile/',
      'https://www.aa.com/aadvantage/',
    ],
  },
  {
    key: 'alaska_air',
    name: 'Alaska Airlines',
    emoji: '✈️',
    loginUrl: 'https://www.alaskaair.com/account/login',
    accountPages: [
      'https://www.alaskaair.com/account/dashboard',
      'https://www.alaskaair.com/account/',
    ],
  },

  // ── Hotels ─────────────────────────────────────────────────────────────────
  {
    key: 'hilton',
    name: 'Hilton Honors',
    emoji: '🏨',
    loginUrl: 'https://www.hilton.com/en/hilton-honors/sign-in/',
    accountPages: [
      'https://www.hilton.com/en/hilton-honors/guest/my-account/',
    ],
  },
  {
    key: 'marriott',
    name: 'Marriott Bonvoy',
    emoji: '🏨',
    loginUrl: 'https://www.marriott.com/sign-in.mi',
    accountPages: [
      'https://www.marriott.com/loyalty/myAccount/default.mi',
      'https://www.marriott.com/loyalty/registrations/default.mi',
      'https://www.marriott.com/loyalty/my-account/home.mi',
    ],
  },
  {
    key: 'hyatt',
    name: 'World of Hyatt',
    emoji: '🏨',
    loginUrl: 'https://world.hyatt.com/content/gp/en/member/loginRegister.html',
    accountPages: [
      'https://www.hyatt.com/en-US/my-account/home',
      'https://www.hyatt.com/en-us/my-account/home',
    ],
  },
  {
    key: 'ihg',
    name: 'IHG One Rewards',
    emoji: '🏨',
    loginUrl: 'https://login.ihg.com/',
    accountPages: [
      'https://www.ihg.com/rewardsclub/content/us/en/member-home',
    ],
  },
  {
    key: 'wyndham',
    name: 'Wyndham Rewards',
    emoji: '🏨',
    loginUrl: 'https://www.wyndhamhotels.com/registry',
    accountPages: [
      'https://www.wyndhamhotels.com/registry',
      'https://www.wyndhamhotels.com/wyndham-rewards/account',
    ],
  },
  {
    key: 'bestwestern',
    name: 'Best Western Rewards',
    emoji: '🏨',
    loginUrl: 'https://www.bestwestern.com/en_US/profile.html',
    accountPages: [
      'https://www.bestwestern.com/en_US/profile.html',
      'https://www.bestwestern.com/en_US/rewards.html',
    ],
  },

  // ── Credit Cards ──────────────────────────────────────────────────────────
  {
    key: 'amex',
    name: 'American Express',
    emoji: '💳',
    loginUrl: 'https://www.americanexpress.com/en-us/account/login',
    accountPages: [
      'https://www.americanexpress.com/en-us/account/',
      'https://www.americanexpress.com/en-us/rewards/membership-rewards/',
    ],
  },
  {
    key: 'chase',
    name: 'Chase',
    emoji: '💳',
    loginUrl: 'https://secure.chase.com/web/auth/dashboard#/dashboard/loginWithChaseButton/index',
    accountPages: [
      'https://secure.chase.com/web/auth/dashboard',
      'https://account.chase.com/consumer/banking/portal',
    ],
  },
  {
    key: 'citi',
    name: 'Citi',
    emoji: '💳',
    loginUrl: 'https://online.citi.com/US/login.do',
    accountPages: [
      'https://online.citi.com/US/JRS/portal/Home.do',
      'https://online.citi.com/US/JRS/pands/detail.do?ID=AccountSummary',
    ],
  },
  {
    key: 'capital_one',
    name: 'Capital One',
    emoji: '💳',
    loginUrl: 'https://verified.capitalone.com/auth/signin',
    accountPages: [
      'https://myaccounts.capitalone.com/accountSummary',
      'https://myaccounts.capitalone.com/rewards',
    ],
  },
];
