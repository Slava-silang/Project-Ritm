pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ScrollView {
    id: pane
    required property var backend
    property var week: backend ? backend.statistics : ({})
    signal openTask(string identifier)
    contentWidth: availableWidth
    clip: true

    ColumnLayout {
        width: pane.availableWidth
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            Button {
                objectName: "previousStatsWeek"
                text: "←"
                Accessible.name: qsTr("Предыдущая неделя")
                enabled: !pane.backend.busy && !!pane.week.start
                onClicked: pane.backend.navigateStatistics(-1)
            }
            Button {
                objectName: "currentStatsWeek"
                text: qsTr("Эта неделя")
                enabled: !pane.backend.busy && !!pane.week.start
                onClicked: pane.backend.navigateStatistics(0)
            }
            Button {
                objectName: "nextStatsWeek"
                text: "→"
                Accessible.name: qsTr("Следующая неделя")
                enabled: !pane.backend.busy && !!pane.week.start
                onClicked: pane.backend.navigateStatistics(1)
            }
            Item { Layout.fillWidth: true }
        }

        Label {
            Layout.fillWidth: true
            text: pane.week.period || ""
            font.pixelSize: 22
            font.weight: Font.DemiBold
        }
        Label {
            objectName: "weeklyProgressLabel"
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: pane.week.taskCount
                ? qsTr("Прогресс недели — %1% · задач в расчёте: %2")
                    .arg(pane.week.percentText).arg(pane.week.taskCount)
                : qsTr("На этой неделе нет задач с дедлайном")
            font.weight: Font.DemiBold
        }
        ProgressBar {
            id: weeklyProgress
            Layout.fillWidth: true
            visible: !!pane.week.taskCount
            from: 0
            to: 100
            value: pane.week.percent || 0
            background: null
            contentItem: Rectangle {
                implicitHeight: 8
                radius: 4
                color: "#e0e7dc"
                Rectangle {
                    width: parent.width * weeklyProgress.position
                    height: parent.height
                    radius: 4
                    color: "#2b5949"
                }
            }
        }
        Flow {
            Layout.fillWidth: true
            spacing: 16
            Label {
                text: qsTr("Завершено за выбранную неделю: %1").arg(pane.week.completedCount || 0)
            }
            Label {
                text: qsTr("Просрочено сейчас: %1").arg(pane.week.overdueCount || 0)
                color: pane.week.overdueCount ? "#a34425" : "#69766d"
            }
        }
        Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            visible: !!pane.week.undatedCount
            text: qsTr("Завершённых задач без даты: %1. Они не входят в недельный счётчик.")
                .arg(pane.week.undatedCount || 0)
            color: "#69766d"
        }

        Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: qsTr("Время фокуса по дням · %1").arg(pane.week.totalFocus || "00:00:00")
            font.pixelSize: 18
            font.weight: Font.DemiBold
        }
        Label {
            text: qsTr("По завершённым сессиям")
            color: "#69766d"
        }
        Repeater {
            model: pane.week.days || []
            delegate: RowLayout {
                id: dayRow
                required property var modelData
                Layout.fillWidth: true
                Label {
                    Layout.preferredWidth: 90
                    text: dayRow.modelData.label
                }
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 8
                    radius: 4
                    color: "#e0e7dc"
                    Rectangle {
                        width: parent.width * dayRow.modelData.fraction
                        height: parent.height
                        radius: 4
                        color: "#2b5949"
                    }
                }
                Label {
                    text: dayRow.modelData.time
                }
            }
        }
        Label {
            Layout.fillWidth: true
            text: qsTr("По задачам")
            font.pixelSize: 18
            font.weight: Font.DemiBold
            visible: !!(pane.week.tasks || []).length
        }
        Repeater {
            model: pane.week.tasks || []
            delegate: RowLayout {
                id: taskRow
                required property var modelData
                Layout.fillWidth: true
                ColumnLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: taskRow.modelData.title
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                        font.weight: Font.DemiBold
                    }
                    Label {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        text: qsTr("Оценка задачи: %1 · Фокус за неделю: %2")
                            .arg(taskRow.modelData.expected
                                ? qsTr("%1 мин").arg(taskRow.modelData.expected) : qsTr("нет"))
                            .arg(taskRow.modelData.time)
                        color: "#69766d"
                    }
                }
                Button {
                    text: qsTr("Открыть")
                    enabled: !pane.backend.busy
                    onClicked: pane.openTask(taskRow.modelData.id)
                }
            }
        }
    }
}
