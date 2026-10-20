# -*- coding: utf-8 -*-
"""归一引擎的测试。"""
import unittest
from src.name_matching.归一引擎 import 清洗名称, 配对候选, 归一分档


class 测试清洗名称(unittest.TestCase):
    def test_去空格和全角(self):
        self.assertEqual(清洗名称("上海语荻　光电有限公司 "), "上海语荻光电有限公司")

    def test_去编码前缀(self):
        self.assertEqual(清洗名称("YF0001_成都精睿恒光电科技有限公司"), "成都精睿恒光电科技有限公司")

    def test_去业务残渣前缀(self):
        self.assertEqual(清洗名称("入库（河北雄安星煜电子科技有限公司"), "河北雄安星煜电子科技有限公司")
        self.assertEqual(清洗名称("冲销中国兵工物资集团有限公司"), "中国兵工物资集团有限公司")

    def test_空值和短名原样返回(self):
        self.assertEqual(清洗名称(""), "")
        self.assertEqual(清洗名称("柏油"), "柏油")


class 测试配对候选(unittest.TestCase):
    def test_简称包含(self):
        对 = 配对候选(["上海语荻光电有限公司", "上海语荻光电科技有限公司"])
        self.assertEqual(len(对), 1)
        self.assertIn("包含", 对[0][2])

    def test_地名开头不同(self):
        对 = 配对候选(["天津满运软件科技有限公司", "宿迁满运软件科技有限公司"])
        self.assertEqual(len(对), 1)
        self.assertIn("地名", 对[0][2])

    def test_疑似笔误(self):
        对 = 配对候选(["成都博立叶光学仪器有限公司", "成都傅立叶光学仪器有限公司"])
        self.assertEqual(len(对), 1)
        self.assertIn("笔误", 对[0][2])

    def test_完全无关不配对(self):
        对 = 配对候选(["天津满运软件科技有限公司", "武侯区哒俗俗商贸部"])
        self.assertEqual(len(对), 0)


class 测试归一分档(unittest.TestCase):
    def test_分档(self):
        结果 = 归一分档(["上海语荻　光电有限公司", "上海语荻光电有限公司", "柏油",
                        "成都博立叶光学仪器有限公司", "成都傅立叶光学仪器有限公司",
                        "天津满运软件科技有限公司", "宿迁满运软件科技有限公司"])
        self.assertEqual(len(结果["自动归一"]), 1)      # 语荻两个写法（空格差别）归一组
        self.assertIn("上海语荻光电有限公司", 结果["自动归一"])
        self.assertEqual(len(结果["候选"]), 2)          # 博/傅笔误、天津/宿迁地名各一对
        self.assertEqual(结果["单独"], ["柏油"])

    def test_简称包含并入自动归一(self):
        结果 = 归一分档(["上海语荻光电有限公司", "上海语荻光电科技有限公司"])
        self.assertEqual(len(结果["自动归一"]), 1)      # 包含且开头一致 → 自动并，规范名取长的
        self.assertIn("上海语荻光电科技有限公司", 结果["自动归一"])
        self.assertEqual(结果["候选"], [])


if __name__ == "__main__":
    unittest.main()
