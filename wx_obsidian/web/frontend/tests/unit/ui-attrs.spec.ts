import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import AppInput from '@/components/ui/AppInput.vue'
import AppNumberInput from '@/components/ui/AppNumberInput.vue'

/**
 * 回归测试：属性透传。
 *
 * 曾经的两个 bug：
 *  1. `AppInput` 改成 `inheritAttrs: false` 后没把 class 合并回根节点，调用方传的
 *     宽度/栅格 class 被静默丢掉；
 *  2. `data-*` / `aria-*` 落在包裹 div 上而不是真正的 input 上（全局「/」快捷键、
 *     label 关联、表单自动化都找不到控件）。
 */
describe('AppInput 属性透传', () => {
  it('class 留在外层容器（布局由调用方决定）', () => {
    const wrapper = mount(AppInput, {
      props: { modelValue: '' },
      attrs: { class: 'w-64' },
    })
    expect(wrapper.classes()).toContain('w-64')
  })

  it('data-* 与 aria-* 落到内部 input 上', () => {
    const wrapper = mount(AppInput, {
      props: { modelValue: '' },
      attrs: { 'data-search-input': '', 'aria-label': '搜索标题', name: 'q' },
    })
    const input = wrapper.find('input')
    expect(input.attributes('data-search-input')).toBeDefined()
    expect(input.attributes('aria-label')).toBe('搜索标题')
    expect(input.attributes('name')).toBe('q')
    expect(wrapper.attributes('data-search-input')).toBeUndefined()
  })

  it('invalid 反映到 aria-invalid', () => {
    const wrapper = mount(AppInput, { props: { modelValue: '', invalid: true } })
    expect(wrapper.find('input').attributes('aria-invalid')).toBe('true')
  })
})

describe('AppNumberInput 属性透传', () => {
  it('id 与 aria-label 落到内部 input（label 才能程序化关联）', () => {
    const wrapper = mount(AppNumberInput, {
      props: { modelValue: 5, id: 'fetch-limit' },
      attrs: { 'aria-label': '处理条数' },
    })
    const input = wrapper.find('input')
    expect(input.attributes('id')).toBe('fetch-limit')
    expect(input.attributes('aria-label')).toBe('处理条数')
  })

  it('预设按钮仍然渲染并可点选', async () => {
    const wrapper = mount(AppNumberInput, { props: { modelValue: 0, presets: [0, 5, 20] } })
    const buttons = wrapper.findAll('button')
    expect(buttons.length).toBeGreaterThanOrEqual(3)
    await buttons[buttons.length - 1].trigger('click')
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([20])
  })
})
