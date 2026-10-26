// NeuroGarden.cs —— B 阶段强交互：手控播种 → 脑控开花
//
// 为什么这么设计（对应导师反馈"操作感太弱、缺少主动操作要素"）：
//   * 手控给你"我做了"的即时反馈：鼠标点地面 → 种子啪地落下（0.3 秒内看见）
//   * 脑控决定"结果"：你放松 → 种子发芽、抽茎、花瓣张开；你紧张 → 花瓣缓慢合拢
//   * 主动操作感来自「手控选目标 + 脑控定成败」，而不是被动等系统喂你一个动画
//
// 铁律（与 NeuroInteraction 一致）：
//   1. 只读 NeuroWorldController.Relax，绝不自己再算一套判断；
//   2. 失败不惩罚：合拢只是变慢变小，花永远不会死、不会消失、不会变色警告；
//   3. 画面上不出现任何数字、分数、百分比、诊断词汇；
//   4. NeuroInteraction.Active = false（P8 对照轮）时整套互动完全停摆：
//      不播种、不生长 —— 对照轮必须是"什么都不响应"。
using System.Collections.Generic;
using UnityEngine;

[ExecuteAlways]
public class NeuroGarden : MonoBehaviour
{
    // ------------------------------------------------------------------ 引用
    [Header("引用（由 NeuroBloom/一键搭建场景 v2 自动接好）")]
    public NeuroWorldController world;
    public Camera cam;
    public Transform gardenRoot;

    [Header("外观资产")]
    public Mesh petalMesh;
    public Mesh stemMesh;          // 半径 0.5 / 高 1 / 原点居中的圆管
    public Mesh blobMesh;          // 种子 / 花心 共用的小球
    public Material stemMat;
    public Material seedMat;
    public Material coreMat;
    public Material[] petalMats;   // 循环取色

    [Header("手控：点击播种")]
    public int   maxPlants     = 20;
    public float plantMinDist  = 1.8f;    // 离相机太近不种（避免糊脸）
    public float plantMaxDist  = 48f;     // 太远不种；地形最远约 40.5 单位，给足余量免得远处点击像失灵
    public float clickCooldown = 0.20f;
    public string groundName   = "Ground";
    public float waterY        = -0.32f;  // 水面高度，仅供参考；绝不能用它判断"水下"（见 HandleClick 注释）
    public float streamHalfWidth = 1.9f;  // 溪流河道半宽：|x-StreamX(z)| 小于它才算溪底

    [Header("脑控：生长")]
    public float growSeconds   = 25f;     // 持续高于个人基线时，种子→盛开所需秒数
    public float baselineTau   = 45f;     // 个人基线的时间常数（秒）
    public float gain          = 10f;     // 基线富余量的放大倍数
    public float retreatFactor = 0.35f;   // 回落系数（< 1 = 合拢比张开更慢、更温和）

    [Header("手动预览（仅出图用；运行时保持关闭）")]
    public bool previewMode = false;
    [Range(0f, 1f)] public float previewGrowth = 0f;
    public Vector3[] previewSpots;        // 由构建器填好（那里才知道地形高度）

    // ------------------------------------------------------------ 外观常量
    private const float PopSeconds     = 0.30f;   // 播种弹出动画时长
    private const int   PetalCount     = 6;
    private const float PetalClosedDeg = -78f;    // 竖直合拢（花苞）
    // ⚠️ 不要改成接近 -12°。相机俯角只有 8°，花瓣一旦摊平就几乎和视线齐平，
    // 被透视压成一条线，画面上看不出是花。-34° 是"碗状微张"，从当前相机会明显看得出花形。
    private const float PetalOpenDeg   = -34f;    // 张开（盛开）

    /// <summary>已种下的株数（只读，供调试/日志用，不显示给使用者）。</summary>
    public int PlantCount { get { return _plants.Count; } }

    /// <summary>诊断/出图用：第 i 株的根节点（越界返回 null）。</summary>
    public Transform PlantRootAt(int i)
    {
        if (i < 0 || i >= _plants.Count) return null;
        return _plants[i].root;
    }

    private class Plant
    {
        public Transform root;
        public Transform stem;
        public Transform head;
        public Transform core;
        public Transform seed;
        public Transform[] petals;
        public float growth;        // 0 = 刚播下，1 = 盛开
        public float pop;          // 0→1 播种弹出
        public float stemHeight;
        public float size;         // 每株整体大小扰动
    }

    private readonly List<Plant> _plants = new List<Plant>();
    private float _baseline = 0.5f;
    private bool  _baselineReady = false;
    private float _clickCd = 0f;

    // 预览重建去重（避免 OnValidate 反复重建把场景弄脏）
    private bool  _bMode = false;
    private float _bGrowth = -1f;
    private int   _bCount = -1;

    private bool Effective { get { return NeuroInteraction.Active && isActiveAndEnabled; } }

    // ==================================================================== 生命周期
    void OnEnable()
    {
        _baselineReady = false;
        _clickCd = 0f;
        EnsureRoot();
        RebuildPreview();
    }

    void OnDisable()
    {
        // 播放时被关掉：绝不把植株留在画面上。
        // 编辑器里不销毁 —— 场景重建时 Unity 会自行清理，此时 DestroyImmediate 不安全。
        if (Application.isPlaying) ClearPlants();
        else _plants.Clear();
    }

    void Update()
    {
        // ---- 编辑器（未播放）时只响应手动预览，不碰任何运行时状态 ----
        if (!Application.isPlaying)
        {
            EnsureRoot();
            RebuildPreview();
            return;
        }

        float dt = Time.deltaTime;

        // ---- P8 对照轮：整套互动完全停摆（不播种、不生长）----
        if (!Effective)
        {
            if (_plants.Count > 0)
            {
                for (int i = 0; i < _plants.Count; i++) ApplyPlant(_plants[i]);
            }
            return;
        }

        if (dt > 0f) HandleClick();

        float relax = (world != null) ? world.Relax : 0f;

        // ---- 个人基线：指数滑动平均（每个人拿自己的水平当参照）----
        if (!_baselineReady) { _baseline = relax; _baselineReady = true; }
        if (dt > 0f)
        {
            float k = 1f - Mathf.Exp(-dt / Mathf.Max(1f, baselineTau));
            _baseline = Mathf.Lerp(_baseline, relax, k);
        }

        // ---- 生长 / 回落 ----
        float surplus = relax - _baseline;
        float step = surplus * dt * gain / Mathf.Max(1f, growSeconds);
        if (step < 0f) step *= Mathf.Max(0.05f, retreatFactor);   // 合拢更慢、更温和

        for (int i = 0; i < _plants.Count; i++)
        {
            var p = _plants[i];
            p.growth = Mathf.Clamp01(p.growth + step);
            p.pop = Mathf.MoveTowards(p.pop, 1f, dt / PopSeconds);
            ApplyPlant(p);
        }

        // ---- 每 5 秒写一条状态日志，方便验证"脑控开花"是否真的在动 ----
        _logT += dt;
        if (_logT >= 5f)
        {
            _logT = 0f;
            float avg = 0f;
            for (int i = 0; i < _plants.Count; i++) avg += _plants[i].growth;
            if (_plants.Count > 0) avg /= _plants.Count;
            Debug.Log("[GARDEN] 放松度=" + Mathf.RoundToInt(relax * 100f) + "% 个人基线=" +
                      Mathf.RoundToInt(_baseline * 100f) + "% 株数=" + _plants.Count +
                      " 平均生长=" + Mathf.RoundToInt(avg * 100f) + "%" +
                      " 鼠标=" + Input.mousePosition + " 按下=" + Input.GetMouseButton(0));
        }
    }

    void OnValidate()
    {
        if (!Application.isPlaying) RebuildPreview();
    }

    // ==================================================================== 手控：播种
    private float _logT;   // 状态日志节流（只写编辑器日志，不进画面）

    /// <summary>
    /// 溪流河道中心线（x 方向）。必须与 NeuroBloomSceneBuilderV2.StreamX 完全一致：
    /// Stream 水面片没有碰撞体，点击会直接打穿到水下地面，只能靠这条中心线把它挡掉。
    /// </summary>
    private static float StreamX(float z)
    {
        return -6.4f + Mathf.Sin(z * 0.16f) * 2.1f;
    }

    private void HandleClick()
    {
        if (_clickCd > 0f) { _clickCd -= Time.deltaTime; return; }
        if (!Input.GetMouseButtonDown(0)) return;

        Camera c = (cam != null) ? cam : Camera.main;
        if (c == null) { Debug.Log("[GARDEN] 点击无效：场景里找不到相机"); return; }

        if (TryPlant(c.ScreenPointToRay(Input.mousePosition), "鼠标"))
            _clickCd = clickCooldown;
    }

    /// <summary>
    /// 控制台（看直播的那一页）上点画面：u,v 是画面里的比例坐标，原点在左上角。
    /// 走这条路不用管 Unity 窗口有没有焦点、是不是被别的窗口挡住了 ——
    /// 用户其实一直在点网页里的画面，点 Unity 窗口那一套很难被发现。
    /// </summary>
    public bool PlantFromViewport(float u, float v)
    {
        if (!Effective) { Debug.Log("[GARDEN] 对照轮，忽略这次播种"); return false; }
        Camera c = (cam != null) ? cam : Camera.main;
        if (c == null) { Debug.Log("[GARDEN] 点击无效：场景里找不到相机"); return false; }
        return TryPlant(c.ViewportPointToRay(new Vector3(u, 1f - v, 0f)), "控制台");
    }

    /// <summary>从一条射线去找能种的地面，找到就种下。source 只进日志。</summary>
    private bool TryPlant(Ray ray, string source)
    {
        if (_plants.Count >= maxPlants)
        {
            Debug.Log("[GARDEN] " + source + "点击无效：已经种满 " + _plants.Count + " 株");
            return false;
        }

        RaycastHit[] hits = Physics.RaycastAll(ray, plantMaxDist + 24f);
        if (hits == null || hits.Length == 0)
        {
            Debug.Log("[GARDEN] " + source + "点击没打中任何东西（视线 " + ray.origin +
                      " → " + ray.direction + "）");
            return false;
        }

        System.Array.Sort(hits, (a, b) => a.distance.CompareTo(b.distance));
        string seen = "";
        for (int i = 0; i < hits.Length; i++)
        {
            var col = hits[i].collider;
            if (col == null) continue;

            string hitName = col.gameObject.name;
            if (seen.Length < 150)
                seen += hitName + "@" + Mathf.RoundToInt(hits[i].distance * 10f) / 10f + " ";

            if (!hitName.StartsWith(groundName)) continue;

            float d = hits[i].distance;
            if (d < plantMinDist || d > plantMaxDist) continue;

            Vector3 pt = hits[i].point;
            // 只挡真正的溪流河道。⚠️ 不要改回"高度低于水面就跳过"：相机脚下和小径的地面
            // 被地形压平到约 -0.5，本来就低于水面线 -0.32，用高度判断会把大片干燥地面误判成
            // 水下（实测 4 次点击只有 3 次能种，用户反馈"只有一块地点了会开花"）。
            if (Mathf.Abs(pt.x - StreamX(pt.z)) < streamHalfWidth) continue;   // 溪底水下不种

            PlantAt(pt, 0f);
            Debug.Log("[GARDEN] 播种成功(" + source + ") 第" + _plants.Count + "株 位置(" +
                      Mathf.RoundToInt(pt.x * 100f) / 100f + ", " +
                      Mathf.RoundToInt(pt.y * 100f) / 100f + ", " +
                      Mathf.RoundToInt(pt.z * 100f) / 100f + ") 距离 " +
                      Mathf.RoundToInt(d * 10f) / 10f);
            return true;
        }

        Debug.Log("[GARDEN] " + source + "点击打中了 [" + seen + "]；但没找到可种的地面（要名字以 " +
                  groundName + " 开头、距离 " + plantMinDist + "~" + plantMaxDist + "、且不在溪流河道里）");
        return false;
    }

    /// <summary>在指定位置种下一株（手控点击与编辑器预览共用）。</summary>
    private Plant PlantAt(Vector3 pos, float initialGrowth)
    {
        if (_plants.Count >= maxPlants) return null;
        EnsureRoot();

        var go = new GameObject("Plant_" + _plants.Count);
        if (gardenRoot != null) go.transform.SetParent(gardenRoot, true);
        go.transform.position = pos;
        go.transform.rotation = Quaternion.Euler(0f, Random.Range(0f, 360f), 0f);

        var p = new Plant();
        p.root = go.transform;
        p.growth = Mathf.Clamp01(initialGrowth);
        p.pop = Application.isPlaying ? 0f : 1f;      // 预览时直接呈最终态
        p.stemHeight = Random.Range(0.36f, 0.62f);
        p.size = Random.Range(0.82f, 1.22f);
        go.transform.localScale = Vector3.one * p.size;

        // 种子（贴地）
        p.seed = MakeChild("Seed", go.transform, blobMesh, seedMat).transform;
        p.seed.localPosition = new Vector3(0f, 0.045f, 0f);

        // 茎（圆管原点居中，故 y = 长度一半；scale.y 直接就是茎高）
        p.stem = MakeChild("Stem", go.transform, stemMesh, stemMat).transform;

        // 花头（茎顶）
        var headGo = new GameObject("Head");
        headGo.transform.SetParent(go.transform, false);
        p.head = headGo.transform;

        p.petals = new Transform[PetalCount];
        for (int i = 0; i < PetalCount; i++)
        {
            var pivot = new GameObject("P" + i);
            pivot.transform.SetParent(headGo.transform, false);
            pivot.transform.localEulerAngles =
                new Vector3(0f, i * (360f / PetalCount) + Random.Range(-6f, 6f), 0f);

            Material mat = null;
            if (petalMats != null && petalMats.Length > 0)
                mat = petalMats[(_plants.Count + i) % petalMats.Length];

            var petal = MakeChild("Petal" + i, pivot.transform, petalMesh, mat);
            petal.transform.localScale = Vector3.one * Random.Range(0.88f, 1.12f);
            p.petals[i] = petal.transform;
        }

        // 花心
        p.core = MakeChild("Core", headGo.transform, blobMesh, coreMat).transform;
        p.core.localPosition = new Vector3(0f, 0.035f, 0f);

        _plants.Add(p);
        ApplyPlant(p);
        return p;
    }

    private void EnsureRoot()
    {
        if (gardenRoot == null)
        {
            var g = GameObject.Find("GardenRoot");
            if (g != null) gardenRoot = g.transform;
        }
    }

    private static GameObject MakeChild(string name, Transform parent, Mesh mesh, Material mat)
    {
        var go = new GameObject(name);
        go.transform.SetParent(parent, false);
        if (mesh != null) go.AddComponent<MeshFilter>().sharedMesh = mesh;
        var mr = go.AddComponent<MeshRenderer>();
        if (mat != null) mr.sharedMaterial = mat;
        mr.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        mr.receiveShadows = false;
        return go;
    }

    // ==================================================================== 脑控：把生长画出来
    /// <summary>
    /// 只用「几何 + 大小」表达生长，不出现任何数字。
    ///   种子 → 茎抽长 → 花瓣从竖直合拢转为平展张开。
    /// </summary>
    private void ApplyPlant(Plant p)
    {
        if (p == null || p.root == null) return;

        float g = Mathf.Clamp01(p.growth);

        // 播种弹出（带一点过冲，手感"啪"地一下）
        float popEase = 1f - Mathf.Pow(1f - Mathf.Clamp01(p.pop), 3f);
        float overshoot = 1f + 0.45f * Mathf.Sin(Mathf.PI * Mathf.Clamp01(p.pop));
        float appear = Mathf.Clamp01(popEase) * overshoot;

        float stemPh = Mathf.Clamp01(g / 0.62f);                 // 0~0.62 抽茎
        float openPh = Mathf.Clamp01((g - 0.50f) / 0.50f);       // 0.50~1.0 开花
        float seedPh = 1f - Mathf.Clamp01(g / 0.18f);            // 种子随抽茎隐去

        // ---- 茎 ----
        float stemH = p.stemHeight * stemPh * appear;
        if (p.stem != null)
        {
            p.stem.localPosition = new Vector3(0f, stemH * 0.5f, 0f);
            p.stem.localScale = new Vector3(0.062f, Mathf.Max(0.0005f, stemH), 0.062f);
            p.stem.gameObject.SetActive(stemH > 0.015f);
        }

        // ---- 花头 ----
        if (p.head != null)
        {
            p.head.localPosition = new Vector3(0f, stemH, 0f);
            float headScale = Mathf.Lerp(0.55f, 1.0f, stemPh) * Mathf.Max(0.001f, appear);
            p.head.localScale = Vector3.one * headScale;
        }

        // ---- 花瓣张开 ----
        float openDeg = Mathf.Lerp(PetalClosedDeg, PetalOpenDeg, openPh);
        if (p.petals != null)
        {
            for (int i = 0; i < p.petals.Length; i++)
            {
                if (p.petals[i] == null) continue;
                p.petals[i].localEulerAngles = new Vector3(openDeg, 0f, 0f);
            }
        }

        // ---- 花心 ----
        if (p.core != null)
        {
            float cs = Mathf.Lerp(0.0008f, 0.055f, stemPh) * Mathf.Max(0.001f, appear);
            p.core.localScale = Vector3.one * cs;
            p.core.gameObject.SetActive(stemPh > 0.12f);
        }

        // ---- 种子 ----
        if (p.seed != null)
        {
            float ss = 0.085f * Mathf.Max(0f, seedPh) * Mathf.Max(0.001f, appear);
            p.seed.localScale = Vector3.one * ss;
            p.seed.gameObject.SetActive(seedPh > 0.02f && stemPh < 0.75f);
        }
    }

    // ==================================================================== 预览 / 清理
    /// <summary>供编辑器出图调用：立刻按当前 previewMode / previewGrowth 重建植株。</summary>
    public void ApplyPreview()
    {
        _bMode = false; _bCount = -1; _bGrowth = -1f;   // 强制重建
        RebuildPreview();
    }

    private void RebuildPreview()
    {
        bool on = previewMode && !Application.isPlaying
                  && previewSpots != null && previewSpots.Length > 0;
        int want = on ? Mathf.Min(previewSpots.Length, maxPlants) : 0;
        float growth = on ? Mathf.Clamp01(previewGrowth) : 0f;

        if (_bMode == on && _bCount == want && Mathf.Abs(_bGrowth - growth) < 1e-4f) return;

        ClearPlants();
        for (int i = 0; i < want; i++) PlantAt(previewSpots[i], growth);

        _bMode = on;
        _bCount = want;
        _bGrowth = growth;
    }

    public void ClearPlants()
    {
        for (int i = 0; i < _plants.Count; i++)
        {
            if (_plants[i] != null && _plants[i].root != null) Kill(_plants[i].root.gameObject);
        }
        _plants.Clear();
        _bMode = false; _bCount = -1; _bGrowth = -1f;
    }

    private static void Kill(Object o)
    {
        if (o == null) return;
        if (Application.isPlaying) Object.Destroy(o); else Object.DestroyImmediate(o);
    }
}
