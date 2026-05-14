### StudyLens

这是一个帮助帝国理工学生学习的产品。

一个学生可能有很多选课。这些选课可以从 https://scientia.doc.ic.ac.uk/2526/timeline 得到。点进去每门课，可以得到 Materials 和 Exercises。Materials 大部分是课程资料，还有一些是Tutorials。爬到这些数据以后分别分类放到一个文件夹下。

对于每一门你找到的课程，可以在 https://imperial.cloud.panopto.eu/Panopto/Pages/Sessions/List.aspx#isSharedWithMe=true 上找到对应的文件夹。（记住，要是今年的），然后可以 download 这个文件夹下的视频。你有可能也可以通过在文本框里 search 这门课程拿到相关视频。

然后，我希望做的是，对视频、课程资料 和 Exercises & Tutorials 做 embedding。

我这个产品支持几个功能：

1. 总体的问答：学生问一个课程相关问题，通过 embedding retrieve 到相关内容以后回答。可以反问学生懂了没，讲几道Exercises / Tutorials 里相关例题 / 造几道和这个知识点强相关（并且最重要的部分只用到这个知识点）的题目。
2. 网页端问答。通过视频的 embedding，搞个网页端插件。支持对这个课程的实时问答。这里不需要反问例题，讲解就行。
3. 生成 cheatsheet。这个需要：1. 从 https://edstem.org/us/dashboard 找到相关课程，看看有没有老师说的 “哪些内容要考，不要考”。对于每门课分别生成 cheatsheet。一定要全面，要每页都看，总结所有知识点，生成 latex。latex 最好是整的两页 A4 纸。要覆盖完整的2页A4纸，并且字体稍微小一些。
4. 生成预测试卷。在 https://exams.doc.ic.ac.uk/ 中可以拿到往年的所有试卷。一般出题风格都差不多。你根据这上面学习一下，预测一下今年的试卷。（登这个网站需要 username 和 password。可以在配置里写。不能硬编码到程序里。）

你要很好地完成这些功能。如果有任何问题解决不了，要问我。