import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { VitePWA } from 'vite-plugin-pwa'

// 判断是否是 Capacitor 构建（APP 打包）
const isCapacitor = process.env.CAPACITOR === 'true'

export default defineConfig({
  plugins: [
    vue(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.ico'],
      manifest: {
        name: '绿邮设备管理',
        short_name: '绿邮管理',
        description: '绿邮X系列内网群控系统 - 设备管理平台',
        theme_color: '#1a1a2e',
        background_color: '#ffffff',
        display: 'standalone',
        orientation: 'portrait',
        start_url: isCapacitor ? './' : '/static/',
        scope: isCapacitor ? './' : '/static/',
        icons: [
          {
            src: isCapacitor ? './pwa-192x192.png' : '/static/pwa-192x192.png',
            sizes: '192x192',
            type: 'image/png'
          },
          {
            src: isCapacitor ? './pwa-512x512.png' : '/static/pwa-512x512.png',
            sizes: '512x512',
            type: 'image/png'
          },
          {
            src: isCapacitor ? './pwa-512x512.png' : '/static/pwa-512x512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable'
          }
        ]
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,png,svg,ico}'],
        runtimeCaching: [
          {
            urlPattern: /^https?:\/\/.*\/api\/devices.*/i,
            handler: 'NetworkFirst',
            options: {
              cacheName: 'api-devices',
              expiration: { maxEntries: 50, maxAgeSeconds: 300 },
              networkTimeoutSeconds: 10
            }
          },
          {
            urlPattern: /^https?:\/\/.*\/api\/(health|config|numbers).*/i,
            handler: 'NetworkFirst',
            options: {
              cacheName: 'api-other',
              expiration: { maxEntries: 30, maxAgeSeconds: 600 },
              networkTimeoutSeconds: 10
            }
          },
          {
            urlPattern: /\.(?:png|svg|ico|css|js)$/,
            handler: 'CacheFirst',
            options: {
              cacheName: 'static-assets',
              expiration: { maxEntries: 100, maxAgeSeconds: 7 * 24 * 3600 }
            }
          }
        ]
      }
    })
  ],
  base: isCapacitor ? './' : '/static/',
  build: {
    outDir: 'dist',
    emptyOutDir: true
  }
})
