import { Role, type MfeManifest } from '@cds/platform'

const manifest: MfeManifest = {
  name: 'admin',
  title: 'Администрирование',
  basePath: '/admin',
  routes: [
    { path: '', redirect: '/admin/tasks' },
    { path: 'tasks', component: () => import('./pages/TasksPage.vue'), meta: { roles: [Role.Viewer] } },
    { path: 'audit', component: () => import('./pages/AuditPage.vue'), meta: { roles: [Role.Operator] } },
    { path: 'users', component: () => import('./pages/UsersPage.vue'), meta: { roles: [Role.Admin] } },
    { path: 'schedules', component: () => import('./pages/SchedulesPage.vue'), meta: { roles: [Role.Admin] } },
    { path: 'logs', component: () => import('./pages/LogsPage.vue'), meta: { roles: [Role.Admin] } },
  ],
  menu: [
    { title: 'Фоновые задачи', path: 'tasks', icon: 'Timer', roles: [Role.Viewer] },
    { title: 'Журнал изменений', path: 'audit', icon: 'Notebook', roles: [Role.Operator] },
    { title: 'Пользователи', path: 'users', icon: 'User', roles: [Role.Admin] },
    { title: 'Расписание задач', path: 'schedules', icon: 'Calendar', roles: [Role.Admin] },
    { title: 'Системные логи', path: 'logs', icon: 'Document', roles: [Role.Admin] },
  ],
}

export default manifest
