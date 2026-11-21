import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import PartitionReadabilityView from '@/views/PartitionReadabilityView.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/partition_readability', name: 'partition_readability', component: PartitionReadabilityView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
