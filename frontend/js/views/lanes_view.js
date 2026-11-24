export const lanesViewTemplate = `
<section class="panel">
  <h2>过磅车道</h2>
  <p class="hint">每车道配置超限阈值与旁路引用；进闸方向与出闸方向分开编码。停用车道落停用禁磅纪要（甲路径，当日锁死），过磅写口零增回滚。</p>
  <div class="form-row">
    <div><label>车道编码</label><input v-model="laneForm.lane_code"/></div>
    <div><label>所属地磅</label>
      <select v-model.number="laneForm.bridge_id">
        <option v-for="b in bridges" :key="b.id" :value="b.id">{{ b.bridge_code }}</option>
      </select>
    </div>
    <div><label>方向</label><input v-model="laneForm.direction" placeholder="进闸/出闸"/></div>
    <div><label>超限阈值(吨)</label><input v-model.number="laneForm.overload_threshold_t" type="number"/></div>
    <div><label>旁路</label><input v-model="laneForm.side_lane_ref"/></div>
  </div>
  <div class="actions">
    <button @click="createLane">开通车道</button>
    <button class="secondary" @click="refresh">刷新</button>
  </div>
  <table class="data-table">
    <thead><tr><th>车道</th><th>地磅</th><th>方向</th><th>阈值</th><th>旁路</th><th>状态</th><th>禁磅裁决</th></tr></thead>
    <tbody>
      <tr v-for="l in lanes" :key="l.id">
        <td>{{ l.lane_code }}</td>
        <td>{{ l.bridge_code }}</td>
        <td>{{ l.direction }}</td>
        <td>{{ l.overload_threshold_t }}t</td>
        <td>{{ l.side_lane_ref || '—' }}</td>
        <td>
          <span v-if="l.status==='停用'" class="badge fail">停用 · 禁磅</span>
          <span v-else-if="l.status==='维护'" class="badge warn">{{ l.status }}</span>
          <span v-else class="badge pass">{{ l.status }}</span>
        </td>
        <td>
          <button v-if="l.status!=='停用'" class="mini-btn" @click="deactivateLane(l.id)">停用禁磅</button>
          <span v-else class="muted">纪要当日锁死</span>
        </td>
      </tr>
    </tbody>
  </table>
</section>
`;
