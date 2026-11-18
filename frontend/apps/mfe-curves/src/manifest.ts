import { Role, type MfeManifest } from '@cds/platform'

const manifest: MfeManifest = {
  name: 'curves',
  title: 'Рыночные данные',
  basePath: '/curves',
  routes: [
    { path: '', component: () => import('./pages/CurvesListPage.vue'), meta: { roles: [Role.Viewer] } },
    { path: 'currencies', component: () => import('./pages/CurrenciesPage.vue'), meta: { roles: [Role.Viewer] } },
    { path: 'new', component: () => import('./pages/CurveDefinitionPage.vue'), meta: { roles: [Role.Operator] } },
    { path: ':code/settings', component: () => import('./pages/CurveDefinitionPage.vue'), meta: { roles: [Role.Operator] } },
    { path: ':code', component: () => import('./pages/CurveWorkspacePage.vue'), meta: { roles: [Role.Viewer] } },
  ],
  menu: [
    { title: 'Процентные кривые', path: '', icon: 'TrendCharts', roles: [Role.Viewer] },
    { title: 'Валюты', path: 'currencies', icon: 'Coin', roles: [Role.Viewer] },
  ],
}

export default manifest
