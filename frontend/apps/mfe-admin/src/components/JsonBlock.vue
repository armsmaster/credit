<template>
  <pre class="json">{{ text }}</pre>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ value: unknown }>()
const text = computed(() => (typeof props.value === 'string' ? props.value : JSON.stringify(props.value, null, 2)))
</script>
