import { Role, type MfeManifest } from '@cds/platform'

const manifest: MfeManifest = {
  name: 'credit',
  title: 'Кредитный риск',
  basePath: '/credit',
  routes: [
    { path: '', component: () => import('./pages/CreditCurvesListPage.vue'), meta: { roles: [Role.Viewer] } },
    { path: 'issuers', component: () => import('./pages/IssuersPage.vue'), meta: { roles: [Role.Viewer] } },
    { path: 'bonds', component: () => import('./pages/BondsPage.vue'), meta: { roles: [Role.Viewer] } },
    { path: 'settings', component: () => import('./pages/SettingsPage.vue'), meta: { roles: [Role.Viewer] } },
    { path: 'cds', component: () => import('./pages/CdsCurvesPage.vue'), meta: { roles: [Role.Viewer] } },
    { path: 'cds/calculator', component: () => import('./pages/CdsCalculatorPage.vue'), meta: { roles: [Role.Viewer] } },
    { path: 'cds/trades', component: () => import('./pages/CdsTradesPage.vue'), meta: { roles: [Role.Viewer] } },
    { path: 'cds/trades/new', component: () => import('./pages/CdsTradeEditPage.vue'), meta: { roles: [Role.Operator] } },
    { path: 'cds/trades/:id/edit', component: () => import('./pages/CdsTradeEditPage.vue'), meta: { roles: [Role.Operator] } },
    { path: 'cds/trades/:id', component: () => import('./pages/CdsTradePage.vue'), meta: { roles: [Role.Viewer] } },
    { path: 'cds/:code', component: () => import('./pages/CdsEntityPage.vue'), meta: { roles: [Role.Viewer] } },
    { path: 'calendars', component: () => import('./pages/CalendarsPage.vue'), meta: { roles: [Role.Viewer] } },
    { path: 'new', component: () => import('./pages/CurveDefinitionPage.vue'), meta: { roles: [Role.Operator] } },
    { path: ':code/settings', component: () => import('./pages/CurveDefinitionPage.vue'), meta: { roles: [Role.Operator] } },
    { path: ':code', component: () => import('./pages/CreditCurveWorkspacePage.vue'), meta: { roles: [Role.Viewer] } },
  ],
  menu: [
    { title: 'Кредитные кривые', path: '', icon: 'DataLine', roles: [Role.Viewer] },
    { title: 'Эмитенты', path: 'issuers', icon: 'OfficeBuilding', roles: [Role.Viewer] },
    { title: 'Облигации', path: 'bonds', icon: 'Tickets', roles: [Role.Viewer] },
    { title: 'CDS-кривые', path: 'cds', icon: 'Histogram', roles: [Role.Viewer] },
    { title: 'Калькулятор CDS', path: 'cds/calculator', icon: 'Operation', roles: [Role.Viewer] },
    { title: 'Сделки CDS', path: 'cds/trades', icon: 'Briefcase', roles: [Role.Viewer] },
    { title: 'Календари рабочих дней', path: 'calendars', icon: 'Calendar', roles: [Role.Viewer] },
    { title: 'Настройки кредитных кривых', path: 'settings', icon: 'Setting', roles: [Role.Viewer] },
  ],
}

export default manifest
