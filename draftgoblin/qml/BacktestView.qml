pragma ComponentBehavior: Bound

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Item {
    id: root

    required property var sessionState
    required property bool narrow

    readonly property var report: sessionState.backtest
    readonly property bool hasReport: report !== null && report !== undefined

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.gutter

        RowLayout {
            Layout.fillWidth: true

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2

                Label {
                    text: "Backtest report"
                    color: Theme.text
                    font.pixelSize: 22
                    font.bold: true
                }
                Label {
                    text: root.report
                        ? root.report.set_code + " · " + root.report.event_name
                            + " · " + root.report.chosen_pick_count + " recorded picks"
                        : "Compare persisted picks with the active ranking"
                    color: Theme.textMuted
                }
            }

            Button {
                text: root.hasReport ? "Run again" : "Run backtest"
                onClicked: sessionProvider.requestBacktest()
            }
        }

        StateBanner {
            Layout.fillWidth: true
            sessionState: root.sessionState
        }

        Rectangle {
            visible: !root.hasReport
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.surfaceLow
            border.color: Theme.outline
            radius: Theme.radius

            ColumnLayout {
                anchors.centerIn: parent
                spacing: 10

                Label {
                    text: "No backtest available"
                    color: Theme.text
                    font.pixelSize: 20
                    font.bold: true
                }
                Label {
                    text: "Complete or recover a draft to run a backtest."
                    color: Theme.textMuted
                }
            }
        }

        RowLayout {
            visible: root.hasReport
            Layout.fillWidth: true
            Layout.preferredHeight: 86
            Layout.minimumHeight: 86
            Layout.maximumHeight: 86
            spacing: 10

            Repeater {
                model: root.report ? [
                    { label: "COMPARED", value: root.report.compared_count },
                    { label: "MATCHED", value: root.report.match_count },
                    { label: "SKIPPED", value: root.report.skipped_count },
                    { label: "RANKING", value: root.report.ranking_mode === "score" ? "DG Score" : root.report.ranking_mode }
                ] : []

                delegate: Rectangle {
                    required property var modelData

                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    color: Theme.surfaceLow
                    border.color: Theme.outline
                    border.width: 1
                    radius: Theme.radius

                    ColumnLayout {
                        anchors.centerIn: parent
                        spacing: 3

                        Label {
                            Layout.alignment: Qt.AlignHCenter
                            text: modelData.value
                            color: modelData.label === "MATCHED" ? Theme.primary : Theme.text
                            font.family: "monospace"
                            font.pixelSize: root.narrow ? 16 : 20
                            font.bold: true
                        }
                        Label {
                            Layout.alignment: Qt.AlignHCenter
                            text: modelData.label
                            color: Theme.textMuted
                            font.pixelSize: 9
                            font.bold: true
                            font.letterSpacing: 0.8
                        }
                    }
                }
            }
        }

        Rectangle {
            visible: root.hasReport
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.surfaceLow
            border.color: Theme.outline
            border.width: 1
            radius: Theme.radius

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 6

                RowLayout {
                    visible: !root.narrow
                    Layout.fillWidth: true

                    Label { Layout.preferredWidth: 74; text: "PICK"; color: Theme.textMuted; font.pixelSize: 10 }
                    Label { Layout.fillWidth: true; text: "RECOMMENDED"; color: Theme.textMuted; font.pixelSize: 10 }
                    Label { Layout.fillWidth: true; text: "ACTUAL"; color: Theme.textMuted; font.pixelSize: 10 }
                    Label { Layout.preferredWidth: 84; text: "RESULT"; color: Theme.textMuted; font.pixelSize: 10 }
                    Label { Layout.preferredWidth: 70; text: "DG"; color: Theme.textMuted; font.pixelSize: 10 }
                    Label { Layout.preferredWidth: 90; text: "SOURCE"; color: Theme.textMuted; font.pixelSize: 10 }
                }

                ListView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 5
                    clip: true
                    model: root.report ? root.report.rows : []
                    Accessible.name: "Backtest pick comparisons"

                    delegate: Rectangle {
                        id: backtestRow
                        required property var modelData
                        readonly property bool skipped: modelData.match === null
                            || modelData.match === undefined

                        width: ListView.view.width
                        height: root.narrow ? 76 : 48
                        color: backtestRow.skipped ? "#30291f" : Theme.surface
                        border.color: Theme.outline
                        border.width: 1
                        radius: Theme.radius

                        RowLayout {
                            visible: !root.narrow
                            anchors.fill: parent
                            anchors.leftMargin: 10
                            anchors.rightMargin: 10
                            spacing: 10

                            Label { Layout.preferredWidth: 74; text: "P" + (modelData.pack_number + 1) + " · P" + (modelData.pick_number + 1); color: Theme.textMuted; font.family: "monospace" }
                            Label { Layout.fillWidth: true; text: modelData.recommended ? modelData.recommended.name : modelData.skipped_reason; color: Theme.text; elide: Text.ElideRight }
                            Label { Layout.fillWidth: true; text: modelData.actual ? modelData.actual.name : "—"; color: Theme.textMuted; elide: Text.ElideRight }
                            Label { Layout.preferredWidth: 84; text: backtestRow.skipped ? "Skipped" : modelData.match ? "Match" : "Different"; color: modelData.match ? Theme.primary : backtestRow.skipped ? Theme.warning : Theme.textMuted; font.bold: true }
                            Label { Layout.preferredWidth: 70; text: modelData.recommended_score !== null && modelData.recommended_score !== undefined ? modelData.recommended_score : "—"; color: Theme.text; font.family: "monospace" }
                            Label { Layout.preferredWidth: 90; text: modelData.data_source || "—"; color: Theme.textMuted; elide: Text.ElideRight }
                        }

                        ColumnLayout {
                            visible: root.narrow
                            anchors.fill: parent
                            anchors.margins: 10
                            spacing: 4

                            RowLayout {
                                Layout.fillWidth: true
                                Label { text: "P" + (modelData.pack_number + 1) + " · P" + (modelData.pick_number + 1); color: Theme.textMuted; font.family: "monospace" }
                                Label { Layout.fillWidth: true; text: backtestRow.skipped ? "Skipped" : modelData.match ? "Match" : "Different"; color: modelData.match ? Theme.primary : Theme.warning; horizontalAlignment: Text.AlignRight }
                            }
                            Label { Layout.fillWidth: true; text: modelData.recommended ? modelData.recommended.name + " → " + (modelData.actual ? modelData.actual.name : "—") : modelData.skipped_reason; color: Theme.text; elide: Text.ElideRight }
                        }
                    }
                }
            }
        }

        Label {
            visible: root.hasReport
            Layout.fillWidth: true
            text: "This is an analytical comparison, not a player grade."
            color: Theme.textMuted
            font.pixelSize: 11
        }
    }
}

