import { Alert, Button, Card, Space, Typography } from 'antd'
import { ClockCircleOutlined } from '@ant-design/icons'
import { PageContainer } from '@ant-design/pro-components'
import { api } from '../api'
import { formatTime, type Shared } from '../shared'

const { Text, Title } = Typography

function SimulationPage({ busy, mutate, clock }: Shared & { clock?: string }) {
  return <PageContainer title="演示时钟" subTitle="调整业务时间；发车、到达和运单事件请在对应业务页面操作。">
    <Card title={<Space><ClockCircleOutlined />当前演示时间</Space>} extra={<Space wrap>
      <Button disabled={busy} onClick={() => mutate('advance:' + clock + ':30', key => api.advance(30, key), '演示时钟已推进 30 分钟')}>推进 30 分钟</Button>
      <Button type="primary" disabled={busy} onClick={() => mutate('advance:' + clock + ':120', key => api.advance(120, key), '演示时钟已推进 2 小时')}>推进 2 小时</Button>
    </Space>}>
      <Title level={2} style={{ marginTop: 0 }}>{formatTime(clock)}</Title>
      <Text type="secondary">推进时钟只更新模拟时间并刷新延误状态，不会自动发车、到达或改变运单阶段。</Text>
    </Card>
    <Alert style={{ marginTop: 16 }} type="info" showIcon message="业务操作已放回订单、运单和运输任务详情页。" />
  </PageContainer>
}

export default SimulationPage
