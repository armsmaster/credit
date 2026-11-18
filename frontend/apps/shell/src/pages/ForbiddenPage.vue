<template>
  <el-result icon="warning" title="Недостаточно прав" sub-title="Для этого раздела нужна другая роль. Обратитесь к администратору.">
    <template #extra>
      <router-link to="/"><el-button type="primary">На главную</el-button></router-link>
    </template>
  </el-result>
</template>
