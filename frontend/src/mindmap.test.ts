import { describe, expect, it } from 'vitest'
import { parseMindmapMarkdown } from './mindmap'

describe('parseMindmapMarkdown', () => {
  it('extracts the knowledge tree and leaves the remaining prose intact', () => {
    const parsed = parseMindmapMarkdown(`## 知识层级树
- 线性代数基础 [S1]
  - 向量空间 [S2]
    - 基底
  - 线性映射

## 脉络关系说明
从向量空间出发理解线性映射。`)

    expect(parsed?.roots).toEqual([{
      id: 'mindmap-node-1',
      text: '线性代数基础',
      citations: [1],
      children: [
        { id: 'mindmap-node-2', text: '向量空间', citations: [2], children: [
          { id: 'mindmap-node-3', text: '基底', citations: [], children: [] },
        ] },
        { id: 'mindmap-node-4', text: '线性映射', citations: [], children: [] },
      ],
    }])
    expect(parsed?.remainder).toContain('## 脉络关系说明')
    expect(parsed?.remainder).toContain('从向量空间出发理解线性映射。')
    expect(parsed?.remainder).not.toContain('线性代数基础')
  })

  it('uses the first multi-level unordered list when the named section is absent', () => {
    const parsed = parseMindmapMarkdown(`# 课程笔记
前置概念：
- 数学基础
- 概率基础

## 主题结构
* 随机变量
  * 离散变量 [S3]

## 补充说明
这里保留原文。`)

    expect(parsed?.roots.map((node) => node.text)).toEqual(['随机变量'])
    expect(parsed?.roots[0].children[0]).toMatchObject({ text: '离散变量', citations: [3] })
    expect(parsed?.remainder).toContain('- 数学基础')
    expect(parsed?.remainder).toContain('## 补充说明')
  })

  it('falls back for flat or malformed lists and ignores fenced code', () => {
    expect(parseMindmapMarkdown('## 知识层级树\n- 只有根节点')).toBeNull()
    expect(parseMindmapMarkdown('```md\n- 示例\n  - 不是导图\n```\n- 正文')).toBeNull()
  })
})
