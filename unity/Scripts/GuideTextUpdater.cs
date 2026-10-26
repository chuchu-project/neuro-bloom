// GuideTextUpdater.cs —— 把"脑境向导"的引导语显示在 VR 世界里的文字牌上
using UnityEngine;

[RequireComponent(typeof(TextMesh))]
public class GuideTextUpdater : MonoBehaviour
{
    public NeuroWorldController world;
    private TextMesh _tm;
    private string _last;

    void Awake() { _tm = GetComponent<TextMesh>(); }

    void Update()
    {
        if (world == null || _tm == null) return;
        var s = world.currentScript;
        if (string.IsNullOrEmpty(s) || s == _last) return;
        _last = s;

        // 每行最多 ~18 个汉字，简单换行
        int perLine = 18;
        var sb = new System.Text.StringBuilder();
        for (int i = 0; i < s.Length; i += perLine)
        {
            sb.Append(s.Substring(i, Mathf.Min(perLine, s.Length - i)));
            sb.Append('\n');
        }
        _tm.text = sb.ToString();
        transform.rotation = Quaternion.LookRotation(transform.position - Camera.main.transform.position);
    }
}
